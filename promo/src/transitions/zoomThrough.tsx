import { AbsoluteFill } from 'remotion';
import type {
  TransitionPresentation,
  TransitionPresentationComponentProps,
} from '@remotion/transitions';

type ZoomThroughProps = Record<string, never>;

// Outgoing shot zooms into the screen and blurs away; incoming starts
// slightly over-scaled and settles to 1 — a "jump through" hero moment.
const ZoomThroughPresentation: React.FC<
  TransitionPresentationComponentProps<ZoomThroughProps>
> = ({ children, presentationDirection, presentationProgress }) => {
  const p = presentationProgress;
  const exiting = presentationDirection === 'exiting';
  const scale = exiting ? 1 + p * 0.9 : 1.3 - p * 0.3;
  const blur = exiting ? p * 16 : (1 - p) * 12;
  const opacity = exiting ? 1 - p * p : Math.min(1, p * 2.2);
  return (
    <AbsoluteFill
      style={{ transform: `scale(${scale})`, filter: `blur(${blur}px)`, opacity }}
    >
      {children}
    </AbsoluteFill>
  );
};

export const zoomThrough = (): TransitionPresentation<ZoomThroughProps> => ({
  component: ZoomThroughPresentation,
  props: {},
});
