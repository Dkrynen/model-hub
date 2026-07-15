// Types mirror the Flask API contract in backend/api.py.

export interface Gpu {
  name: string;
  vram_gb: number;
  backend: string;
  tier: string;
  device_index: number;
}
export interface ComputeTier {
  name: string;
  memory_gb: number;
  backend: string;
  kind: string;       // "discrete" | "integrated" | "ram"
  device_index: number;
}
export interface ScanInfo {
  os: string;
  cpu: string;
  cores: number;
  ram_gb: number;
  gpus: Gpu[];
  total_vram_gb: number;
  combined_vram_gb: number;
  compute_tiers: ComputeTier[];
  is_apple_silicon: boolean;
  in_container: boolean;
}

export interface Scores {
  quality: number;
  speed: number;
  fit: number;
  context: number;
}
export interface TierAllocation {
  kind: string;
  name: string;
  memory_gb: number;
  allocated_gb: number;
  backend: string;
  device_index: number;
  bandwidth: number;
  layers: number;
}
export interface SplitPlan {
  run_mode: string;
  summary: string;
  total_model_gb: number;
  total_layers: number;
  gpu_layers: number;
  env_vars: Record<string, string>;
  tiers: TierAllocation[];
}
export interface Recommendation {
  name: string;
  model_id: string;
  provider: string;
  params_b: number;
  quant: string;
  score: number;
  vram_gb: number;
  context: number;
  run_mode: string;
  ollama_cmd: string;
  speed_source: "measured" | "calibrated" | "estimated";
  speed_band_pct: number;
  scores: Scores;
  split_plan: SplitPlan | null;
}
export interface RecommendResponse {
  vram_gb: number;
  combined_vram_gb: number;
  ram_gb: number;
  recommendations: Recommendation[];
}

export interface CatalogModel {
  id: string;
  name: string;
  provider: string;
  params_b: number;
  arch: string;
  context: number;
  use_cases: string[];
  is_moe: boolean;
  vram_q4: number;
  vram_q8: number;
  vram_f16: number;
}

export interface InstalledModel {
  name: string;
  size_gb: number;
  modified: string;
  digest_short: string;
}
export interface OllamaModelProfile extends InstalledModel {
  digest: string;
  format: string | null;
  family: string | null;
  families: string[] | null;
  parameter_size: string | null;
  quantization_level: string | null;
  context_length: number | null;
}
export interface OllamaModelProfilesResponse {
  profiles: OllamaModelProfile[];
}
export interface OllamaStatus {
  running: boolean;
  version: string | null;
  error?: string;
}
export interface RunningModel {
  name: string;
  size_gb: number;
  digest_short: string;
}
export interface PsResponse {
  running: boolean;
  models: RunningModel[];
}

export interface LibraryModel {
  name: string;
  description?: string;
  capabilities?: string[];
  sizes?: string[];
  pulls?: string;
  tag_count?: string;
  vram_q4?: number;
  params_b?: number;
  display?: string;
  fit?: string;
}
export interface LibraryBrowseResponse {
  total: number;
  system_vram: number | null;
  models: LibraryModel[];
  error?: string;
}
export interface TagsResponse {
  name: string;
  tags: string[];
  count: number;
  error?: string;
}

export interface HfGgufModel {
  repo_id: string;
  author?: string;
  downloads: number;
  likes: number;
  gated: boolean;
  last_modified?: string;
  tags: string[];
  license?: string;
  base_model?: string;
  pipeline_tag?: string;
  gguf_files: number;
  quants: string[];
  files: HfGgufFile[];
  recommended_quant?: string;
  recommended_file?: string;
  recommended_size_gb?: number;
  fit: "fits" | "offload" | "too_large" | "unknown";
  vram_gb?: number;
  preflight?: ImportPreflight;
}
export interface HfGgufFile {
  filename: string;
  selection: string;
  quant?: string;
  size_bytes?: number;
  size_gb?: number;
  fit: "fits" | "offload" | "too_large" | "unknown";
  vram_gb?: number;
  importable: boolean;
  compatibility_note?: string | null;
  preflight?: ImportPreflight;
}
export interface HfGgufSearchResponse {
  query: string;
  total: number;
  system_vram?: number | null;
  ram_gb?: number | null;
  models: HfGgufModel[];
  error?: string;
}

export interface InstallPreflightResponse {
  target: string;
  kind: "ollama" | "hf_gguf" | "hf_unknown";
  action: "pull" | "import";
  state: "ok" | "blocked" | "unknown" | "error";
  normalized: string;
  message: string;
  repo_id?: string;
  model_ref?: string;
  source_url?: string;
  selector?: string | null;
  gated?: boolean;
  selected_file?: string;
  selected_quant?: string;
  selected_size_bytes?: number;
  selected_size_gb?: number;
  fit?: "fits" | "offload" | "too_large" | "unknown";
  vram_gb?: number;
  recommended_file?: string;
  recommended_quant?: string;
  model_store_dir?: string;
  model_store?: SpaceCheck;
  preflight?: ImportPreflight;
  warnings: string[];
}

export interface SpaceCheck {
  ok: boolean;
  free_bytes: number | null;
  free_gb: number | null;
  required_bytes: number;
  required_gb: number | null;
}

export interface ImportPreflight {
  state: "ok" | "blocked" | "unknown";
  scratch_dir: string;
  model_store_dir: string;
  shared_volume?: boolean | null;
  combined?: SpaceCheck | null;
  selected_size_bytes?: number;
  selected_size_gb?: number;
  moves_existing_models?: boolean;
  scratch: SpaceCheck;
  model_store: SpaceCheck;
  warnings: string[];
}

export interface PerformanceSignal {
  kind: string;
  severity: "success" | "info" | "warning" | "danger";
  label: string;
  value_ms?: number;
  tokens_per_second?: number | null;
}

export interface PerformanceAction {
  kind: string;
  label: string;
}

export interface PerformanceDiagnosis {
  state: "ok" | "watch" | "slow" | "unmeasured";
  summary: string;
  signals: PerformanceSignal[];
  actions: PerformanceAction[];
}

export interface PerformanceMetrics {
  model: string;
  protocol_id?: string;
  num_ctx?: number;
  prompt?: string;
  num_predict?: number;
  eval_count?: number;
  eval_duration_ms?: number;
  total_duration_ms?: number;
  load_duration_ms?: number;
  prompt_eval_duration_ms?: number;
  time_to_first_token_ms?: number;
  tokens_per_second?: number;
  response?: string;
  source?: string;
  timestamp?: number;
}

export interface PerformanceDiagnosticsResponse {
  model: string | null;
  installed_models: string[];
  installed_models_reported: boolean;
  running_models: string[];
  running_models_reported: boolean;
  history: PerformanceMetrics[];
  latest: PerformanceMetrics | null;
  diagnosis: PerformanceDiagnosis;
}

export interface PerformanceProbeResponse {
  model: string;
  state: "done" | "failed";
  metrics?: PerformanceMetrics;
  diagnosis?: PerformanceDiagnosis;
  error?: string;
}

export interface DownloadEntry {
  model?: string;
  status?: string;
  timestamp?: string | number;
  state?: string;
  percent?: number;
  completed?: number;
  total?: number;
  updated_at?: number;
  [k: string]: unknown;
}

export interface PullStatusEntry {
  model: string;
  state: string;
  status?: string;
  completed?: number;
  total?: number;
  percent?: number;
  started_at?: number;
  updated_at?: number;
  error?: string;
  [k: string]: unknown;
}

export interface PullStatusResponse {
  active: number;
  pulls: PullStatusEntry[];
}

export interface AptConfig {
  workspace: string;
  ollama_host: string;
  theme: string;
  default_model: string;
}

export interface WorkspaceInfo {
  id: string;
  name: string;
  description: string;
}

export interface ProjectInfo {
  id: string;
  workspace: string;
  name: string;
  description: string;
  root: string;
  status: "active";
  created_at: number;
  updated_at: number;
}

export interface ProjectRegistrationInput {
  name: string;
  description?: string;
  root: string;
}

export interface ProjectFileEntry {
  name: string;
  type: "dir" | "file";
  size: number;
}

export interface ProjectFilesResponse {
  path: string;
  entries: ProjectFileEntry[];
  truncated: boolean;
}

export interface ProjectFileDetail {
  path: string;
  content: string;
  sha256: string;
  size: number;
}

export interface SessionMessage {
  role: "system" | "user" | "assistant";
  content: string;
  timestamp?: number;
}

export interface SessionEvent {
  id?: number;
  type: string;
  payload: Record<string, unknown>;
  timestamp?: number;
}

export interface SessionSummary {
  id: string;
  name: string;
  model: string;
  system_prompt: string;
  workspace: string;
  project_id: string | null;
  created_at: number;
  updated_at: number;
}

export interface SessionDetail extends SessionSummary {
  context: string;
  messages: SessionMessage[];
  events: SessionEvent[];
}

export interface AgentChatPayload {
  agent: "ask" | "plan" | "explore" | "build";
  model: string;
  message: string;
  messages?: SessionMessage[];
  session_id?: string;
  project_id?: string;
  name?: string;
}

export type AgentApprovalDecision = "allow" | "deny";

export interface AgentApprovalAnswerBody {
  ask_id: string;
  decision: AgentApprovalDecision;
  remember: boolean;
}

export interface AgentApprovalAnswerRequest extends AgentApprovalAnswerBody {
  approval_token: string;
}

export interface AgentApprovalAnswerResponse {
  ok: true;
}

export interface AgentRunCancelResponse {
  ok: true;
}

export interface AgentSandboxStatus {
  backend: "docker";
  available: boolean;
  code: string;
  message: string;
  tasks: string[];
  image?: string | null;
  network: "none";
}

export interface SandboxTaskApprovalTarget {
  kind: "sandbox_task";
  name: string;
  argv: string[];
  root: string;
  image: string;
  image_id: string;
  timeout_seconds: number;
  network: "none";
  staged_overlay_digest: string;
  config_digest: string;
  staged_changes: SandboxTaskStagedChangeTarget[];
}

export interface SandboxTaskStagedChangeTarget {
  id: string;
  path: string;
  base_hash: string | null;
  updated_at: number;
  content_hash: string;
}

export type StagedChangeStatus =
  | "pending"
  | "applied"
  | "rejected"
  | "conflict"
  | "reverted";

export interface StagedChangeSummary {
  id: string;
  session_id: string;
  run_id: string;
  root: string;
  path: string;
  base_hash: string | null;
  status: StagedChangeStatus;
  created_at: number;
  updated_at: number;
  new_size: number;
}

export interface StagedChangeDetail extends Omit<StagedChangeSummary, "new_size"> {
  old_content: string | null;
  new_content: string;
}

export interface StagedChangesResponse {
  changes: StagedChangeSummary[];
}

export interface StagedChangeActionResponse {
  status: string;
  path?: string;
  current?: string;
  error?: string;
  disk_hash?: string | null;
  base_hash?: string | null;
}

export interface StagedBatchApplyResponse {
  applied: string[];
  conflicts: string[];
  errors: { id: string; error: string }[];
}

export interface ProjectFileSaveRequest {
  path: string;
  content: string;
  base_sha256: string | null;
}

export interface ProjectFileSaveResponse {
  status: "applied";
  change_id: string;
  path: string;
  sha256: string;
  size: number;
}

export interface ProjectFileSaveConflict {
  error: "conflict";
  code: "save_conflict";
  disk_sha256: string | null;
}

export interface VersionInfo {
  version: string;
  github_url: string;
  download_url: string;
  app_name: string;
}

export interface StorageInfo {
  app_dir: string;
  app_size_bytes: number | null;
  ollama_models_dir: string;
  ollama_models_size_bytes: number | null;
  ollama_models_configured: boolean;
  ollama_models_user_dir?: string | null;
  ollama_models_user_configured?: boolean;
  ollama_models_restart_required?: boolean;
  model_weight_files_in_app: { path: string; size_bytes: number }[];
  models_are_bundled: boolean;
  model_install_mode: "on_demand_ollama_pull";
}

export interface ModelLocationInfo {
  state: string;
  platform: string;
  env_var: "OLLAMA_MODELS";
  configured: boolean;
  configured_dir: string | null;
  process_configured: boolean;
  process_dir: string;
  default_dir: string;
  effective_after_restart: string;
  current_size_bytes: number | null;
  configured_size_bytes: number | null;
  restart_ollama_required: boolean;
  restart_lac_required: boolean;
  moves_existing_models: boolean;
}

export interface ModelStoreDoctorAction {
  kind: string;
  label: string;
  severity: "info" | "warning" | "danger";
}

export interface ModelStoreDiskInfo {
  path?: string;
  exists?: boolean;
  active?: boolean;
  size_bytes?: number | null;
  size_gb?: number | null;
  free_bytes?: number | null;
  free_gb?: number | null;
  total_bytes?: number | null;
  total_gb?: number | null;
  used_bytes?: number | null;
  used_gb?: number | null;
}

export interface ImportScratchInfo extends ModelStoreDiskInfo {
  entries?: number | null;
  safe_to_clear: boolean;
}

export interface ModelStoreDoctor {
  state: "ok" | "watch" | "critical";
  warnings: string[];
  actions: ModelStoreDoctorAction[];
  model_store: ModelStoreDiskInfo & { path: string };
  import_scratch: ImportScratchInfo & { path: string };
  default_model_store: ModelStoreDiskInfo & { path: string; active: boolean };
  app_payload: {
    path: string;
    size_bytes: number | null;
    size_gb?: number | null;
    model_weight_files: { path: string; size_bytes: number }[];
  };
}

export interface ImportScratchClearResponse {
  state: "cleared" | "failed";
  path: string;
  deleted_entries?: number;
  deleted_bytes?: number;
  error?: string;
}

export type ProPlan = "pro" | "pro_local" | "pro_cloud" | "dev";

export interface ProStatus {
  licensed: boolean;
  plan?: ProPlan | null;
  expires_human?: string | null;
  machine?: string | null;
  checked?: string | null;
}
