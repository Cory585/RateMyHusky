import { Composition } from 'remotion';
import { PromoVideo } from './PromoVideo';
import { FPS, TOTAL_FRAMES } from './timing';

export const RemotionRoot: React.FC = () => (
  <Composition
    id="PromoVideo"
    component={PromoVideo}
    durationInFrames={TOTAL_FRAMES}
    fps={FPS}
    width={1920}
    height={1080}
  />
);
