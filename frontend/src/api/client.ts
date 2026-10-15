// The API, typed from its schema. Every response is {"data": ...}; every error {"error": {"code", "message"}}.
import createClient from "openapi-fetch";
import type { paths } from "./schema";

export const client = createClient<paths>({ baseUrl: "" });

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
  ) {
    super(message);
  }
}

let unauthorized: () => void = () => {};
/** What to do when a write needs a session: the app opens the sign-in dialog. */
export function onUnauthorized(handler: () => void): void {
  unauthorized = handler;
}

interface Result<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

function failure(result: Result<unknown>): ApiError {
  const body = result.error as { error?: { message?: string; code?: string } } | undefined;
  return new ApiError(
    result.response.status,
    body?.error?.message ?? `HTTP ${result.response.status}`,
    body?.error?.code,
  );
}

/** The data of a response, or an ApiError. */
export async function unwrap<T>(request: Promise<Result<{ data: T }>>): Promise<T> {
  const result = await request;
  if (result.response.status === 401) unauthorized();
  if (result.error !== undefined || result.data === undefined) throw failure(result);
  return result.data.data;
}

/** For writes answered with 204. */
export async function done(request: Promise<Result<unknown>>): Promise<void> {
  const result = await request;
  if (result.response.status === 401) unauthorized();
  if (!result.response.ok) throw failure(result);
}
