/** Development-only lifecycle counters; no geometry, user or provider data is retained. */
export const sceneResources = { activeViewers: 0, activeHandlers: 0, activeEntities: 0,
  activeFieldBytes: 0, mounts: 0, unmounts: 0, updates: 0 };
if (import.meta.env.DEV) window.__urbanAirSceneResources = sceneResources;
