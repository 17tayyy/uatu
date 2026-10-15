// A Sentinel-2 photo of a detection's place, laid on the map where it belongs, to fade against the radar.
import { Image as ImageIcon, X } from "lucide-react";
import type { ImageSource } from "maplibre-gl";
import { useEffect, useState } from "react";
import { fetchOptical } from "../../api/queries";
import type { OpticalImage } from "../../api/types";
import { useUi } from "../../app/store";
import { duration, when } from "../../lib/format";
import type { LngLat } from "../../lib/geo";
import { fitBounds, getMap } from "../../map/instance";
import { Button, Spinner } from "../../ui";
import s from "./inspector.module.css";

type Corners = [[number, number], [number, number], [number, number], [number, number]];

function caption(image: OpticalImage): string {
  const minutes = Math.abs(image.minutes_from_radar);
  const gap = minutes < 48 * 60 ? duration(minutes) : `${Math.round(minutes / 1440)} días`;
  const km = (image.size_m / 1000).toLocaleString("es-ES");
  return [
    `Sentinel-${image.platform.replace("sentinel-", "").toUpperCase()}`,
    when(image.acquired_at),
    `${gap} ${image.minutes_from_radar >= 0 ? "después" : "antes"} del radar`,
    `nubes ${Math.round(image.cloud * 100)} %`,
    `${km} × ${km} km`,
  ].join(" · ");
}

export function Optical({ detectionId, lngLat }: { detectionId: number; lngLat: LngLat }) {
  const shown = useUi((u) => u.optical);
  const showOptical = useUi((u) => u.showOptical);
  const [state, setState] = useState<"idle" | "busy" | "none" | string>("idle");
  const here = shown && shown.lngLat[0] === lngLat[0] && shown.lngLat[1] === lngLat[1];

  const look = async () => {
    setState("busy");
    try {
      const image = await fetchOptical(detectionId);
      if (!image) return setState("none");
      setState("idle");
      showOptical({ image, lngLat });
    } catch (e) {
      setState(`No se pudo buscar: ${(e as Error).message}`);
    }
  };

  return (
    <div className={s.optical}>
      {here ? (
        <span className={s.caption}>Su foto óptica está en el mapa.</span>
      ) : state === "busy" ? (
        <span className={s.caption}>
          <Spinner /> Buscando la foto más clara de ±3 días…
        </span>
      ) : (
        <>
          <Button size="small" onClick={look}>
            <ImageIcon size={13} /> Foto óptica (Sentinel-2)
          </Button>
          <span className={s.caption}>
            {state === "none"
              ? "No hay ninguna foto de Sentinel-2 sin nubes de este sitio en ±3 días."
              : state !== "idle"
                ? state
                : "Sentinel-2 pasa hacia mediodía: un barco en marcha ya no estará; uno fondeado, una plataforma o una granja, sí."}
          </span>
        </>
      )}
    </div>
  );
}

/** The photo on the map, and the card to fade or remove it. */
export function OpticalCard() {
  const optical = useUi((u) => u.optical);
  const showOptical = useUi((u) => u.showOptical);
  const [opacity, setOpacity] = useState(100);

  useEffect(() => {
    const map = getMap();
    if (!map) return;
    if (!optical) {
      if (map.getLayer("optical")) map.removeLayer("optical");
      if (map.getSource("optical")) map.removeSource("optical");
      return;
    }
    const corners = optical.image.corners as Corners;
    const source = map.getSource("optical") as ImageSource | undefined;
    if (source) source.updateImage({ url: optical.image.url, coordinates: corners });
    else {
      map.addSource("optical", { type: "image", url: optical.image.url, coordinates: corners });
      map.addLayer({ id: "optical", type: "raster", source: "optical", paint: { "raster-fade-duration": 0 } }, "coast");
    }
    setOpacity(100);
    const lons = corners.map((c) => c[0]);
    const lats = corners.map((c) => c[1]);
    fitBounds(
      [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ],
      14,
    );
  }, [optical]);

  useEffect(() => {
    const map = getMap();
    if (map?.getLayer("optical")) map.setPaintProperty("optical", "raster-opacity", opacity / 100);
  }, [opacity]);

  if (!optical) return null;
  return (
    <div className={s.opticalCard}>
      <div className={s.opticalTop}>
        <span>
          <strong>Foto óptica</strong> · {caption(optical.image)}
        </span>
        <button type="button" onClick={() => showOptical(null)} aria-label="Quitar la foto" title="Quitar la foto">
          <X size={15} />
        </button>
      </div>
      <label className={s.opacity}>
        Opacidad
        <input type="range" min={0} max={100} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} />
      </label>
    </div>
  );
}
