export type CompanionSkinManifest = {
  version: 1;
  id: string;
  name: string;
  renderer: 'core-eye' | 'image';
  asset?: string;
  capabilities: readonly ('emotion' | 'audio-level' | 'gaze')[];
  interactionRegions: readonly { x: number; y: number; width: number; height: number }[];
};

// Assets are data rendered by this application, never executable plugins.
// The controller owns menus, drag, capture permissions and session projection.
export const COMPANION_SKINS: readonly CompanionSkinManifest[] = [
  { version: 1, id: 'core-eye', name: 'Core Eye', renderer: 'core-eye',
    capabilities: ['emotion', 'audio-level', 'gaze'],
    interactionRegions: [{ x: 0, y: 0, width: 192, height: 192 }] },
  { version: 1, id: 'soft-orb', name: 'Soft Orb', renderer: 'image', asset: '/skins/soft-orb.svg',
    capabilities: ['emotion', 'audio-level'],
    interactionRegions: [{ x: 0, y: 0, width: 192, height: 192 }] },
];

export function skinManifest(id: unknown): CompanionSkinManifest {
  return COMPANION_SKINS.find(skin => skin.id === id) || COMPANION_SKINS[0];
}
