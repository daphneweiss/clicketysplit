// Typed fetch wrappers around the Flask /api/* routes.
//
// Every wrapper throws `ApiError` on `!response.ok`, populated from the
// backend's structured `{ error: { code, message, ... } }` envelope (see
// 04_BACKEND_API.md §Error handling). Call sites typically catch and surface
// the message via the toast store.

import {
  ApiError,
  type Capabilities,
  type DetectAllResponse,
  type DetectResponse,
  type DiscoveryResult,
  type ExperimentConfig,
  type ExportResponse,
  type ResolveBrowseResponse,
  type SegmentsFile,
  type SessionPayload,
  type StimulusListResponse,
  type StimulusListsResponse,
} from "./types";

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function throwFromResponse(response: Response): Promise<never> {
  const body = await readBody(response);
  if (body && typeof body === "object" && "error" in body) {
    const err = (body as { error: Record<string, unknown> }).error;
    const code = typeof err.code === "string" ? err.code : "http_error";
    const message =
      typeof err.message === "string"
        ? err.message
        : `HTTP ${response.status}`;
    const extras = { ...err };
    delete extras.code;
    delete extras.message;
    throw new ApiError(code, message, response.status, extras);
  }
  const message =
    typeof body === "string" && body
      ? body
      : `HTTP ${response.status} ${response.statusText}`;
  throw new ApiError("http_error", message, response.status);
}

async function jsonGet<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) await throwFromResponse(response);
  return (await readBody(response)) as T;
}

async function jsonPost<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(body ?? {}),
  });
  if (!response.ok) await throwFromResponse(response);
  return (await readBody(response)) as T;
}

// ---- Capabilities & meta --------------------------------------------------

export function getCapabilities(): Promise<Capabilities> {
  return jsonGet<Capabilities>("/api/capabilities");
}

// ---- Config ---------------------------------------------------------------

export function getConfig(): Promise<ExperimentConfig> {
  return jsonGet<ExperimentConfig>("/api/config");
}

export function loadConfig(
  path: string,
): Promise<{ status: "ok"; config: ExperimentConfig }> {
  return jsonPost("/api/config/load", { path });
}

export function writeConfig(
  configDir: string,
  config: ExperimentConfig,
  overwrite = false,
): Promise<{ status: "ok"; path: string }> {
  // Strip backend-injected fields before sending (the POST validator rejects
  // unknown top-level keys when pydantic is strict — even though our model
  // currently allows extras, prefer to send only declared fields).
  const { _experiment_path, _config_dir, ...rest } = config;
  return jsonPost("/api/config", {
    config_dir: configDir,
    config: rest,
    overwrite,
  });
}

// ---- Discovery ------------------------------------------------------------

export function discoverRecordings(root: string): Promise<DiscoveryResult> {
  return jsonPost<DiscoveryResult>("/api/discover", { root });
}

export function resolveBrowse(name: string): Promise<ResolveBrowseResponse> {
  return jsonPost<ResolveBrowseResponse>("/api/resolve_browse", { name });
}

export function pickFolder(opts?: {
  initial_dir?: string;
  title?: string;
}): Promise<{ path: string | null }> {
  return jsonPost<{ path: string | null }>("/api/pick_folder", opts ?? {});
}

// ---- Stimulus lists -------------------------------------------------------

export function listStimulusLists(): Promise<StimulusListsResponse> {
  return jsonGet<StimulusListsResponse>("/api/stimulus_lists");
}

export function getStimulusList(name: string): Promise<StimulusListResponse> {
  return jsonGet<StimulusListResponse>(
    `/api/stimulus_lists/${encodeURIComponent(name)}`,
  );
}

// ---- Detection ------------------------------------------------------------

export function detect(
  speakerId: string,
  condition: string,
  force = false,
): Promise<DetectResponse> {
  return jsonPost<DetectResponse>("/api/detect", {
    speaker_id: speakerId,
    condition,
    force,
  });
}

export function detectAll(
  speakers?: string[],
  conditions?: string[],
  force = false,
): Promise<DetectAllResponse> {
  const body: Record<string, unknown> = { force };
  if (speakers !== undefined) body.speakers = speakers;
  if (conditions !== undefined) body.conditions = conditions;
  return jsonPost<DetectAllResponse>("/api/detect_all", body);
}

// ---- Segments -------------------------------------------------------------

export function getSegments(
  speakerId: string,
  condition: string,
): Promise<SegmentsFile> {
  return jsonGet<SegmentsFile>(
    `/api/segments/${encodeURIComponent(speakerId)}/${encodeURIComponent(condition)}`,
  );
}

export function saveSegments(
  speakerId: string,
  condition: string,
  payload: SegmentsFile,
): Promise<{ status: "ok"; path: string }> {
  return jsonPost(
    `/api/segments/${encodeURIComponent(speakerId)}/${encodeURIComponent(condition)}`,
    payload,
  );
}

// ---- Export ---------------------------------------------------------------

export function exportTokens(
  speakerId: string,
  condition: string,
  selectedTokens?: Record<string, number[]> | null,
): Promise<ExportResponse> {
  return jsonPost<ExportResponse>("/api/export", {
    speaker_id: speakerId,
    condition,
    selected_tokens: selectedTokens ?? null,
  });
}

export function exportAll(
  speakers?: string[],
  conditions?: string[],
  selections?: Record<string, Record<string, Record<string, number[]>>>,
): Promise<{ status: "ok"; results: ExportResponse[] }> {
  const body: Record<string, unknown> = {};
  if (speakers !== undefined) body.speakers = speakers;
  if (conditions !== undefined) body.conditions = conditions;
  if (selections !== undefined) body.selections = selections;
  return jsonPost("/api/export_all", body);
}

// ---- Session --------------------------------------------------------------

export function getSession(): Promise<SessionPayload> {
  return jsonGet<SessionPayload>("/api/session");
}

export function saveSession(
  data: SessionPayload,
  autosave = false,
): Promise<{ status: "ok"; path: string }> {
  return jsonPost("/api/session", { ...data, autosave });
}
