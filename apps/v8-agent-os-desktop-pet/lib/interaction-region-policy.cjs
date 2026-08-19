const MAX_INTERACTION_REGIONS = 4;
const MAX_REGION_EDGE = 960;
const MAX_TOTAL_REGION_AREA = 1_200_000;
const REGION_PADDING = 24;

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function initialSafeShape(bounds = {}) {
  const width = Math.max(1, Math.floor(finiteNumber(bounds.width) || 1));
  const height = Math.max(1, Math.floor(finiteNumber(bounds.height) || 1));
  return [{ x: width - 1, y: height - 1, width: 1, height: 1 }];
}

function normalizeInteractionRegions(regions, bounds = {}) {
  const width = Math.max(1, Math.floor(finiteNumber(bounds.width) || 0));
  const height = Math.max(1, Math.floor(finiteNumber(bounds.height) || 0));
  if (!Array.isArray(regions) || regions.length < 1 || regions.length > MAX_INTERACTION_REGIONS) return [];

  const normalized = [];
  let totalArea = 0;
  for (const region of regions) {
    if (!region || typeof region !== 'object') return [];
    const x = finiteNumber(region.x);
    const y = finiteNumber(region.y);
    const regionWidth = finiteNumber(region.width);
    const regionHeight = finiteNumber(region.height);
    if (x === null || y === null || regionWidth === null || regionHeight === null) return [];
    if (regionWidth <= 0 || regionHeight <= 0) return [];
    if (regionWidth + REGION_PADDING * 2 > MAX_REGION_EDGE || regionHeight + REGION_PADDING * 2 > MAX_REGION_EDGE) return [];

    const left = Math.max(0, Math.floor(x - REGION_PADDING));
    const top = Math.max(0, Math.floor(y - REGION_PADDING));
    const right = Math.min(width, Math.ceil(x + regionWidth + REGION_PADDING));
    const bottom = Math.min(height, Math.ceil(y + regionHeight + REGION_PADDING));
    if (right <= left || bottom <= top) return [];

    const item = { x: left, y: top, width: right - left, height: bottom - top };
    totalArea += item.width * item.height;
    if (totalArea > MAX_TOTAL_REGION_AREA) return [];
    normalized.push(item);
  }
  return normalized;
}

module.exports = {
  initialSafeShape,
  normalizeInteractionRegions,
};
