// Server data: fetched, cached and refreshed by TanStack Query. One hook per thing the dashboard shows.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { REFRESH_MS } from "../lib/constants";
import { client, done, unwrap } from "./client";
import type { EventFeature, FeatureCollection, ReviewWrite, WatchCreate } from "./types";

const forever = { staleTime: Number.POSITIVE_INFINITY, gcTime: Number.POSITIVE_INFINITY };
const live = { refetchInterval: REFRESH_MS, refetchIntervalInBackground: false };

// --- the coast ---------------------------------------------------------------------------------

export const useZones = () =>
  useQuery({ queryKey: ["zones"], queryFn: () => unwrap(client.GET("/api/v1/zones")), ...forever });

export const useLand = () =>
  useQuery({ queryKey: ["land"], queryFn: () => unwrap(client.GET("/api/v1/map/land")), ...forever });

export const useSatellite = () =>
  useQuery({ queryKey: ["satellite"], queryFn: () => unwrap(client.GET("/api/v1/map/satellite")), ...forever });

export const useInfrastructure = (zone: string, enabled: boolean) =>
  useQuery({
    queryKey: ["infrastructure", zone],
    queryFn: () => unwrap(client.GET("/api/v1/map/infrastructure", { params: { query: { zone } } })),
    enabled,
    staleTime: 3_600_000,
  });

export const useWaters = (zone: string, enabled: boolean) =>
  useQuery({
    queryKey: ["waters", zone],
    queryFn: () => unwrap(client.GET("/api/v1/map/waters", { params: { query: { zone } } })),
    enabled,
    staleTime: 3_600_000,
  });

export const useCoverage = (zone: string, enabled: boolean) =>
  useQuery({
    queryKey: ["coverage", zone],
    queryFn: () => unwrap(client.GET("/api/v1/coverage", { params: { query: { zone, days: 7 } } })),
    enabled,
    staleTime: 600_000,
  });

export const useDensity = (zone: string, enabled: boolean) =>
  useQuery({
    queryKey: ["density", zone],
    queryFn: () => unwrap(client.GET("/api/v1/history/density", { params: { query: { zone, days: 7 } } })),
    enabled,
    staleTime: 600_000,
  });

export const useUnimaged = (zone: string, passId: string | null) =>
  useQuery({
    queryKey: ["unimaged", zone, passId],
    queryFn: () =>
      unwrap(client.GET("/api/v1/map/unimaged", { params: { query: { zone, ...(passId ? { pass: passId } : {}) } } })),
    staleTime: 600_000,
  });

// --- now ----------------------------------------------------------------------------------------

export const useStatus = (zone: string) =>
  useQuery({
    queryKey: ["status", zone],
    queryFn: () => unwrap(client.GET("/api/v1/status", { params: { query: { zone } } })),
    ...live,
  });

export const useReports = () =>
  useQuery({ queryKey: ["reports"], queryFn: () => unwrap(client.GET("/api/v1/reports")), staleTime: 3_600_000 });

export const useLive = (zone: string) =>
  useQuery({
    queryKey: ["live", zone],
    queryFn: () => unwrap(client.GET("/api/v1/vessels/live", { params: { query: { zone, minutes: 60 } } })),
    placeholderData: keepPreviousData,
    ...live,
  });

export const useEvents = (zone: string, hours: number, minSeverity: number) =>
  useQuery({
    queryKey: ["events", zone, hours, minSeverity],
    queryFn: () =>
      unwrap(
        client.GET("/api/v1/events", { params: { query: { zone, hours, min_severity: minSeverity, limit: 1000 } } }),
      ) as Promise<FeatureCollection<EventFeature["properties"]>>,
    placeholderData: keepPreviousData,
    ...live,
  });

// --- radar ----------------------------------------------------------------------------------------

export const usePasses = (zone: string) =>
  useQuery({
    queryKey: ["passes", zone],
    queryFn: () => unwrap(client.GET("/api/v1/passes", { params: { query: { zone, limit: 60 } } })),
    ...live,
  });

export const usePass = (passId: string | null) =>
  useQuery({
    queryKey: ["pass", passId],
    queryFn: () => unwrap(client.GET("/api/v1/passes/{pass_id}", { params: { path: { pass_id: passId ?? "" } } })),
    enabled: passId != null,
    staleTime: 60_000,
  });

export const usePending = (zone: string) =>
  useQuery({
    queryKey: ["pending", zone],
    queryFn: () =>
      unwrap(client.GET("/api/v1/detections/pending", { params: { query: { zone, days: 30, limit: 500 } } })),
    ...live,
  });

export async function fetchOptical(detectionId: number) {
  return unwrap(
    client.GET("/api/v1/detections/{detection_id}/optical", { params: { path: { detection_id: detectionId } } }),
  );
}

// --- vessels ---------------------------------------------------------------------------------------

export const useVessel = (mmsi: number | null) =>
  useQuery({
    queryKey: ["vessel", mmsi],
    queryFn: () =>
      unwrap(
        client.GET("/api/v1/vessels/{mmsi}", { params: { path: { mmsi: mmsi ?? 0 }, query: { track_hours: 24 } } }),
      ),
    enabled: mmsi != null,
    staleTime: 60_000,
  });

export const useVesselSearch = (q: string) =>
  useQuery({
    queryKey: ["vessel-search", q],
    queryFn: () => unwrap(client.GET("/api/v1/vessels", { params: { query: { q, limit: 30 } } })),
    enabled: q.length >= 2,
    placeholderData: keepPreviousData,
  });

export const useCompany = (id: string | null) =>
  useQuery({
    queryKey: ["company", id],
    queryFn: () => unwrap(client.GET("/api/v1/companies/{company_id}", { params: { path: { company_id: id ?? "" } } })),
    enabled: id != null,
    staleTime: 3_600_000,
  });

export const useWatches = () =>
  useQuery({ queryKey: ["watches"], queryFn: () => unwrap(client.GET("/api/v1/watches")), staleTime: 60_000 });

// --- the time machine --------------------------------------------------------------------------------

export const useHistoryRange = (enabled: boolean) =>
  useQuery({
    queryKey: ["history-range"],
    queryFn: () => unwrap(client.GET("/api/v1/history")),
    enabled,
    staleTime: 300_000,
  });

export function historyChunk(zone: string, start: number) {
  return {
    queryKey: ["history", zone, start],
    queryFn: () =>
      unwrap(
        client.GET("/api/v1/history/tracks", {
          params: { query: { zone, start: new Date(start).toISOString(), hours: 1 } },
        }),
      ),
    // A past hour does not change; the current one does.
    staleTime: start + 3_600_000 < Date.now() - 300_000 ? Number.POSITIVE_INFINITY : 60_000,
    gcTime: 15 * 60_000,
  };
}

// --- signing in, and what it lets you change ----------------------------------------------------------

export const useSession = () =>
  useQuery({ queryKey: ["session"], queryFn: () => unwrap(client.GET("/api/v1/auth/session")), staleTime: 300_000 });

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { username: string; password: string }) => unwrap(client.POST("/api/v1/auth/login", { body })),
    onSuccess: (session) => qc.setQueryData(["session"], session),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => done(client.POST("/api/v1/auth/logout")),
    onSettled: () => qc.setQueryData(["session"], { username: null }),
  });
}

/** After a verdict: the pass, the queue and the counts. */
function invalidateReviews(qc: ReturnType<typeof useQueryClient>, passId: string) {
  qc.invalidateQueries({ queryKey: ["pass", passId] });
  qc.invalidateQueries({ queryKey: ["pending"] });
  qc.invalidateQueries({ queryKey: ["passes"] });
}

export function usePutReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; passId: string; body: ReviewWrite }) =>
      unwrap(client.PUT("/api/v1/detections/{detection_id}/review", { params: { path: { detection_id: id } }, body })),
    onSuccess: (_, { passId }) => invalidateReviews(qc, passId),
  });
}

export function useDeleteReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: number; passId: string }) =>
      done(client.DELETE("/api/v1/detections/{detection_id}/review", { params: { path: { detection_id: id } } })),
    onSuccess: (_, { passId }) => invalidateReviews(qc, passId),
  });
}

function invalidateWatches(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["watches"] });
  qc.invalidateQueries({ queryKey: ["vessel"] });
  qc.invalidateQueries({ queryKey: ["live"] });
}

export function useAddWatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: WatchCreate) => unwrap(client.POST("/api/v1/watches", { body })),
    onSuccess: () => invalidateWatches(qc),
  });
}

export function useRemoveWatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      done(client.DELETE("/api/v1/watches/{watch_id}", { params: { path: { watch_id: id } } })),
    onSuccess: () => invalidateWatches(qc),
  });
}
