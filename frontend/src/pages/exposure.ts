/** 曝光三角的 EV100 换算；超出器材范围时限制滑块值以保持等效曝光。 */

export interface Exposure {
  aperture: number;
  shutter: number;
  iso: number;
}

export type ExposureControl = keyof Exposure;

const APERTURE_MIN = 1;
const APERTURE_MAX = 22;
const SHUTTER_MIN = 1 / 8000;
const SHUTTER_MAX = 30;
const ISO_MIN = 50;
const ISO_MAX = 25600;

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

/** 按 ISO 100 标准计算曝光值，ISO 增加一档时 EV100 减少一档。 */
export function exposureValue({ aperture, shutter, iso }: Exposure): number {
  return Math.log2((aperture * aperture) / shutter) - Math.log2(iso / 100);
}

/** 保持原 EV100，优先保留用户调整的滑块，再联动其余参数。 */
export function changeExposure(
  previous: Exposure,
  control: ExposureControl,
  requested: number,
): Exposure {
  const power = 2 ** exposureValue(previous);
  if (control === "aperture") {
    const aperture = clamp(
      requested,
      Math.max(APERTURE_MIN, Math.sqrt(SHUTTER_MIN * power * (ISO_MIN / 100))),
      Math.min(APERTURE_MAX, Math.sqrt(SHUTTER_MAX * power * (ISO_MAX / 100))),
    );
    const preferredShutter = (aperture * aperture) / (power * (previous.iso / 100));
    const shutter = clamp(preferredShutter, SHUTTER_MIN, SHUTTER_MAX);
    const iso = 100 * (aperture * aperture) / (power * shutter);
    return { aperture, shutter, iso };
  }
  if (control === "shutter") {
    const shutter = clamp(
      requested,
      Math.max(SHUTTER_MIN, (APERTURE_MIN * APERTURE_MIN) / (power * (ISO_MAX / 100))),
      Math.min(SHUTTER_MAX, (APERTURE_MAX * APERTURE_MAX) / (power * (ISO_MIN / 100))),
    );
    const preferredAperture = Math.sqrt(shutter * power * (previous.iso / 100));
    const aperture = clamp(preferredAperture, APERTURE_MIN, APERTURE_MAX);
    const iso = 100 * (aperture * aperture) / (power * shutter);
    return { aperture, shutter, iso };
  }
  const iso = clamp(
    requested,
    Math.max(ISO_MIN, 100 * (APERTURE_MIN * APERTURE_MIN) / (power * SHUTTER_MAX)),
    Math.min(ISO_MAX, 100 * (APERTURE_MAX * APERTURE_MAX) / (power * SHUTTER_MIN)),
  );
  const preferredShutter = (previous.aperture * previous.aperture) / (power * (iso / 100));
  const shutter = clamp(preferredShutter, SHUTTER_MIN, SHUTTER_MAX);
  const aperture = Math.sqrt(shutter * power * (iso / 100));
  return { aperture, shutter, iso };
}
