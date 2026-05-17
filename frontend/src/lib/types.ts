// TypeScript types mirroring the backend JSON shapes.
//
// Field names and defaults match the pydantic models in
// src/clicketysplit/config.py and the dataclasses in
// src/clicketysplit/detection/base.py and src/clicketysplit/discovery.py.
// If you change one side, change the other.

// ---- Config ---------------------------------------------------------------

export type PresentationOrder = "random" | "cycled" | "blocked";
export type DetectorBackend = "silero" | "webrtc" | "energy";
export type AudioFormat = "wav" | "flac";

export interface Speaker {
  id: string;
  // Backend fills this from id when omitted; we mirror that by allowing null
  // on the wire and keeping it optional in the wizard until we commit.
  subdir?: string | null;
}

export interface Condition {
  name: string;
  stimulus_list: string;
  presentation_order: PresentationOrder;
  expected_reps_per_stimulus: number;
}

export interface DetectionConfig {
  backend: DetectorBackend;
  vad_threshold: number;
  min_segment_ms: number;
  min_silence_ms: number;
  silence_margin_ms: number;
  denoise: boolean;
}

export interface LabelingConfig {
  min_word_duration_ms: number;
  max_word_duration_ms: number;
  drop_intro_block: boolean;
}

export interface ExportConfig {
  pad_ms: number;
  fade_ms: number;
  format: AudioFormat;
  produce_csv: boolean;
  produce_textgrid: boolean;
}

export interface ExperimentConfig {
  schema_version: number;
  name: string;
  recordings_root: string;
  stimulus_lists_root: string;
  output_root: string;
  speakers: Speaker[];
  conditions: Condition[];
  detection: DetectionConfig;
  labeling: LabelingConfig;
  export: ExportConfig;

  // Added by the backend GET /api/config response (never sent to the
  // server). Optional because we synthesize empty configs locally before
  // any save.
  _experiment_path?: string;
  _config_dir?: string;
}

// ---- Capabilities (GET /api/capabilities) ---------------------------------

export interface Capabilities {
  detectors: DetectorBackend[];
  denoise_available: boolean;
  textgrid_available: boolean;
  audio_formats: string[];
}

// ---- Discovery (POST /api/discover) ---------------------------------------

export interface ScannedCondition {
  name: string;
  n_files: number;
  total_bytes: number;
  files: string[];
}

export interface ScannedSpeaker {
  id: string;
  subdir: string;
  conditions: ScannedCondition[];
  flat_files: string[];
}

export interface DiscoveryResult {
  root: string;
  speakers: ScannedSpeaker[];
  unique_condition_names: string[];
}

// ---- Segments -------------------------------------------------------------

export type SegmentType = "word" | "short_noise" | "crosstalk" | "intro";
export type SegmentStatus = "pending" | "accepted" | "rejected" | "intro";
export type LabelSource = "" | "auto" | "anchor";
export type AnchorSource = "initial" | "user";

export interface Segment {
  start: number;
  end: number;
  duration_ms: number;
  segment_type: SegmentType;
  assigned_name: string;
  label_source: LabelSource;
  status: SegmentStatus;
  token_index: number;
  cluster_size: number;
}

export interface LabelAnchor {
  word_index: number;
  label: string;
  source: AnchorSource;
}

export interface SegmentsFile {
  schema_version?: number;
  segments: Segment[];
  stimulus_list?: string[];
  /** Python and the spec both call this `label_anchors`; older docs used
   * `anchors`. We accept both on input and write `label_anchors` on save. */
  anchors?: LabelAnchor[];
  label_anchors?: LabelAnchor[];
  source_files?: Array<{ path: string; offset_sec: number; duration_sec: number }>;
  _source?: "reviewed" | "proposed";
  [extra: string]: unknown;
}

// ---- Stimulus lists -------------------------------------------------------

export interface StimulusListsResponse {
  stimulus_lists_root: string;
  files: string[];
}

export interface StimulusListResponse {
  name: string;
  lines: string[];
}

// ---- Detection responses --------------------------------------------------

export interface DetectResponse {
  status: "ok";
  speaker_id: string;
  condition: string;
  n_segments: number;
  n_words: number;
  audio_duration_sec: number;
  detector: DetectorBackend;
  stimulus_list: string[];
  overview_png: string;
}

export interface DetectAllResultOk extends DetectResponse {}
export interface DetectAllResultErr {
  status: "error";
  speaker_id: string;
  condition: string;
  error: { code: string; message: string; [extra: string]: unknown };
}
export type DetectAllResult = DetectAllResultOk | DetectAllResultErr;

export interface DetectAllResponse {
  status: "ok";
  results: DetectAllResult[];
}

// ---- Export responses -----------------------------------------------------

export interface ExportResponse {
  status: "ok";
  speaker_id: string;
  condition: string;
  n_exported: number;
  tokens_per_word: Record<string, number>;
  output_dir: string;
  files: string[];
  manifest: string;
  csv: string | null;
  skipped?: Record<string, number>;
}

// ---- Resolve browse -------------------------------------------------------

export interface ResolveBrowseResponse {
  matches: string[];
}

// ---- Session --------------------------------------------------------------

export interface SessionPayload {
  step?: 1 | 2 | 3 | 4;
  activeSpeakerId?: string | null;
  activeCondition?: string | null;
  lastRecordingsRoot?: string | null;
  lastConfigPath?: string | null;
  [extra: string]: unknown;
}

// ---- API errors / toasts --------------------------------------------------

export type ToastKind = "info" | "success" | "warn" | "error";

export interface Toast {
  id: number;
  message: string;
  kind: ToastKind;
}

export class ApiError extends Error {
  code: string;
  status: number;
  extras: Record<string, unknown>;
  constructor(
    code: string,
    message: string,
    status: number,
    extras: Record<string, unknown> = {},
  ) {
    super(message);
    this.code = code;
    this.status = status;
    this.extras = extras;
    this.name = "ApiError";
  }
}
