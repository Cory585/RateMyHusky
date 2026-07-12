export const FPS = 30;

// Scene lengths in frames at 30fps.
export const SCENES = {
  intro: 80,
  search: 130,
  professor: 160,
  ask: 145,
  compare: 130,
  courses: 160,
  darkmode: 70,
  outro: 100,
} as const;

// Transition lengths in frames, keyed by the scene each one leads INTO.
// Whip pans 10, zoom-through 12, fades 5-8. Transitions overlap scenes,
// so total = sum(SCENES) - sum(TRANSITIONS) = 914 (~30.5s).
export const TRANSITIONS = {
  search: 6,
  professor: 10,
  ask: 12,
  compare: 10,
  courses: 10,
  darkmode: 5,
  outro: 8,
} as const;

const ids = Object.keys(SCENES) as (keyof typeof SCENES)[];
export const TOTAL_FRAMES =
  ids.reduce((sum, id) => sum + SCENES[id], 0) -
  Object.values(TRANSITIONS).reduce((a, b) => a + b, 0);
