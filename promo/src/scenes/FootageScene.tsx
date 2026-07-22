import { AbsoluteFill, OffthreadVideo, staticFile } from 'remotion';
import { Caption } from '../components/Caption';

// Plays one captured clip, speed-fitted or trimmed to its scene slot.
export const FootageScene: React.FC<{
  src: string;
  startFrom?: number; // frames into the source clip
  playbackRate?: number;
  caption?: string;
}> = ({ src, startFrom = 0, playbackRate = 1, caption }) => (
  <AbsoluteFill style={{ backgroundColor: '#0f0f10' }}>
    <OffthreadVideo
      src={staticFile(src)}
      startFrom={startFrom}
      playbackRate={playbackRate}
      muted
    />
    {caption ? <Caption text={caption} /> : null}
  </AbsoluteFill>
);
