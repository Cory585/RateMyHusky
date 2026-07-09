export const FPS = 30;
export const TRANSITION_FRAMES = 12;

// Scene lengths in frames at 30fps. Transitions overlap scenes by
// TRANSITION_FRAMES, so total = sum - 7 * TRANSITION_FRAMES = 846 (28.2s).
export const SCENES = {
  intro: 80,
  search: 130,
  professor: 160,
  ask: 145,
  compare: 130,
  courses: 115,
  darkmode: 70,
  outro: 100,
} as const;

const ids = Object.keys(SCENES) as (keyof typeof SCENES)[];
export const TOTAL_FRAMES =
  ids.reduce((sum, id) => sum + SCENES[id], 0) -
  TRANSITION_FRAMES * (ids.length - 1);
