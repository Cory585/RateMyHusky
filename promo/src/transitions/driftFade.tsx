import { AbsoluteFill } from 'remotion';
import type {
  TransitionPresentation,
  TransitionPresentationComponentProps,
} from '@remotion/transitions';

type DriftFadeProps = Record<string, never>;

// Crossfade with a slight scale drift so the cut breathes instead of
// sitting still.
const DriftFadePresentation: React.FC<
  TransitionPresentationComponentProps<DriftFadeProps>
> = ({ children, presentationDirection, presentationProgress }) => {
  const p = presentationProgress;
  const exiting = presentationDirection === 'exiting';
  const scale = exiting ? 1 + p * 0.04 : 0.97 + p * 0.03;
  return (
    <AbsoluteFill
      style={{ opacity: exiting ? 1 : p, transform: `scale(${scale})` }}
    >
      {children}
    </AbsoluteFill>
  );
};

export const driftFade = (): TransitionPresentation<DriftFadeProps> => ({
  component: DriftFadePresentation,
  props: {},
});
