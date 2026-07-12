import { AbsoluteFill, Series } from 'remotion';
import { DARK } from './branding';
import { Caption } from './components/Caption';
import { IntroCard } from './scenes/IntroCard';
import { OutroCard } from './scenes/OutroCard';
import { SCENES, TOTAL_FRAMES } from './timing';

export const PromoVideo: React.FC = () => (
  <AbsoluteFill style={{ backgroundColor: DARK }}>
    <Series>
      <Series.Sequence durationInFrames={SCENES.intro}>
        <IntroCard />
      </Series.Sequence>
      <Series.Sequence durationInFrames={TOTAL_FRAMES - SCENES.intro - SCENES.outro}>
        <AbsoluteFill style={{ backgroundColor: DARK }}>
          <Caption text="Caption preview" />
        </AbsoluteFill>
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENES.outro}>
        <OutroCard />
      </Series.Sequence>
    </Series>
  </AbsoluteFill>
);
