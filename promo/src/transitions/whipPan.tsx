import { useMemo, useState } from 'react';
import { AbsoluteFill, random } from 'remotion';
import type {
  TransitionPresentation,
  TransitionPresentationComponentProps,
} from '@remotion/transitions';

type WhipPanProps = { width: number };

// Both slides sweep left as one continuous pan; horizontal-only blur
// (SVG feGaussianBlur) peaks at the cut midpoint.
const WhipPanPresentation: React.FC<
  TransitionPresentationComponentProps<WhipPanProps>
> = ({ children, presentationDirection, presentationProgress, passedProps }) => {
  const p = presentationProgress;
  const x =
    presentationDirection === 'exiting'
      ? -p * passedProps.width
      : (1 - p) * passedProps.width;
  const blur = Math.sin(p * Math.PI) * 40;
  const [filterId] = useState(() => `whip-${random(null)}`);
  const style: React.CSSProperties = useMemo(
    () => ({
      transform: `translateX(${x}px)`,
      filter: blur > 0.5 ? `url(#${filterId})` : undefined,
    }),
    [x, blur, filterId]
  );
  return (
    <AbsoluteFill>
      <svg width="0" height="0">
        <defs>
          <filter id={filterId}>
            <feGaussianBlur stdDeviation={`${blur},0`} />
          </filter>
        </defs>
      </svg>
      <AbsoluteFill style={style}>{children}</AbsoluteFill>
    </AbsoluteFill>
  );
};

export const whipPan = (
  props: WhipPanProps
): TransitionPresentation<WhipPanProps> => ({
  component: WhipPanPresentation,
  props,
});
