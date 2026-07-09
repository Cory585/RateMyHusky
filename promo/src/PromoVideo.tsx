import {
  AbsoluteFill,
  Audio,
  Easing,
  interpolate,
  staticFile,
  useVideoConfig,
} from 'remotion';
import { TransitionSeries, linearTiming } from '@remotion/transitions';
import { fade } from '@remotion/transitions/fade';
import manifest from './manifest.json';
import { DARK, MUSIC } from './branding';
import { AskScene } from './scenes/AskScene';
import { FootageScene } from './scenes/FootageScene';
import { IntroCard } from './scenes/IntroCard';
import { OutroCard } from './scenes/OutroCard';
import { driftFade } from './transitions/driftFade';
import { whipPan } from './transitions/whipPan';
import { zoomThrough } from './transitions/zoomThrough';
import { FPS, SCENES, TOTAL_FRAMES, TRANSITIONS } from './timing';

type SceneName = keyof typeof manifest.scenes;

// Speed up a clip so its full length fits the scene slot (capped at 2x;
// anything past the slot after capping just gets cut by the sequence end).
const fitRate = (name: SceneName, frames: number) => {
  const d = manifest.scenes[name].durationSec;
  return Math.min(2, Math.max(1, d / (frames / FPS)));
};

const soft = (frames: number) => linearTiming({ durationInFrames: frames });
const snap = (frames: number) =>
  linearTiming({
    durationInFrames: frames,
    easing: Easing.bezier(0.65, 0, 0.35, 1),
  });

export const PromoVideo: React.FC = () => {
  const { width } = useVideoConfig();
  const dm = manifest.scenes.darkmode;
  const darkStart = Math.max(
    0,
    Math.round((dm.markers.toggledAt - 0.8) * FPS)
  );
  // compare.mp4 and courses.mp4 have no markers, and their slots (4.3s /
  // 3.8s) are much shorter than the clips (12.6s / 8.9s), so fitRate's 2x
  // cap would still land mid-typing / mid-navigation. Both clips end on
  // their settled, most-illustrative state (populated comparison table;
  // course page scrolled to the Rating History chart), so anchor startFrom
  // to play the clip's tail: end of clip lines up with end of slot.
  const compareStart =
    Math.round(manifest.scenes.compare.durationSec * FPS) - SCENES.compare;
  const coursesStart =
    Math.round(manifest.scenes.courses.durationSec * FPS) - SCENES.courses;
  return (
    <AbsoluteFill style={{ backgroundColor: DARK }}>
      <TransitionSeries>
        <TransitionSeries.Sequence durationInFrames={SCENES.intro}>
          <IntroCard />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={driftFade()} timing={soft(TRANSITIONS.search)} />
        <TransitionSeries.Sequence durationInFrames={SCENES.search}>
          <FootageScene
            src={manifest.scenes.search.file}
            playbackRate={fitRate('search', SCENES.search)}
            caption="Find any professor in seconds"
          />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={whipPan({ width })} timing={snap(TRANSITIONS.professor)} />
        <TransitionSeries.Sequence durationInFrames={SCENES.professor}>
          <FootageScene
            src={manifest.scenes.professor.file}
            playbackRate={fitRate('professor', SCENES.professor)}
            caption="Every review source. One page."
          />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={zoomThrough()} timing={snap(TRANSITIONS.ask)} />
        <TransitionSeries.Sequence durationInFrames={SCENES.ask}>
          <AskScene />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={whipPan({ width })} timing={snap(TRANSITIONS.compare)} />
        <TransitionSeries.Sequence durationInFrames={SCENES.compare}>
          <FootageScene
            src={manifest.scenes.compare.file}
            startFrom={compareStart}
            caption="Compare head-to-head"
          />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={whipPan({ width })} timing={snap(TRANSITIONS.courses)} />
        <TransitionSeries.Sequence durationInFrames={SCENES.courses}>
          <FootageScene
            src={manifest.scenes.courses.file}
            startFrom={coursesStart}
            caption="Browse every course and department"
          />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={fade()} timing={soft(TRANSITIONS.darkmode)} />
        <TransitionSeries.Sequence durationInFrames={SCENES.darkmode}>
          <FootageScene src={dm.file} startFrom={darkStart} />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={fade()} timing={soft(TRANSITIONS.outro)} />
        <TransitionSeries.Sequence durationInFrames={SCENES.outro}>
          <OutroCard />
        </TransitionSeries.Sequence>
      </TransitionSeries>
      <Audio
        src={staticFile(MUSIC)}
        volume={(f) =>
          interpolate(
            f,
            [0, 24, TOTAL_FRAMES - 60, TOTAL_FRAMES - 6],
            [0, 1, 1, 0],
            { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }
          )
        }
      />
    </AbsoluteFill>
  );
};
