import { AbsoluteFill } from 'remotion';
import { DARK, FONT, RED } from './branding';

export const PromoVideo: React.FC = () => (
  <AbsoluteFill
    style={{
      backgroundColor: DARK,
      justifyContent: 'center',
      alignItems: 'center',
    }}
  >
    <div style={{ color: RED, fontFamily: FONT, fontSize: 80, fontWeight: 900 }}>
      RateMyHusky
    </div>
  </AbsoluteFill>
);
