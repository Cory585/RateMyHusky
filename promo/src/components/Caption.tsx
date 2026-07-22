import { spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { FONT, RED } from '../branding';

export const Caption: React.FC<{ text: string }> = ({ text }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - 8, fps, config: { damping: 200 } });
  return (
    <div
      style={{
        position: 'absolute',
        bottom: 70,
        width: '100%',
        display: 'flex',
        justifyContent: 'center',
        opacity: s,
        transform: `translateY(${(1 - s) * 40}px)`,
      }}
    >
      <div
        style={{
          background: RED,
          color: '#fff',
          fontFamily: FONT,
          fontWeight: 800,
          fontSize: 44,
          padding: '14px 36px',
          borderRadius: 999,
          boxShadow: '0 8px 30px rgba(0,0,0,.35)',
        }}
      >
        {text}
      </div>
    </div>
  );
};
