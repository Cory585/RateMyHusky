import { Config } from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);
Config.setJpegQuality(95);
Config.setCrf(16);
// Tag + convert the deliverable to bt709 limited range; without this the
// JPEG-sourced pipeline ships full-range with a bt601 matrix tag, which
// strict players and social transcodes render washed/hue-shifted.
Config.setColorSpace('bt709');
