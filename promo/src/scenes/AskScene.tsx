import { AbsoluteFill, OffthreadVideo, Series, staticFile } from 'remotion';
import manifest from '../manifest.json';
import { Caption } from '../components/Caption';
import { FPS, SCENES } from '../timing';

// Part A: the last ~2s of typing up to the Enter press.
// Part B: jump straight to the answer (skips the LLM wait).
export const AskScene: React.FC = () => {
  const m = manifest.scenes.ask;
  const partA = Math.round(SCENES.ask * 0.45);
  const typingStart = Math.max(
    0,
    Math.round((m.markers.typedAt + 0.6) * FPS) - partA
  );
  const answerStart = Math.round((m.markers.answerAt + 0.2) * FPS);
  return (
    <AbsoluteFill style={{ backgroundColor: '#0f0f10' }}>
      <Series>
        <Series.Sequence durationInFrames={partA}>
          <OffthreadVideo
            src={staticFile(m.file)}
            startFrom={typingStart}
            muted
          />
        </Series.Sequence>
        <Series.Sequence durationInFrames={SCENES.ask - partA}>
          <OffthreadVideo
            src={staticFile(m.file)}
            startFrom={answerStart}
            muted
          />
        </Series.Sequence>
      </Series>
      <Caption text="Ask anything. Get cited answers." />
    </AbsoluteFill>
  );
};
