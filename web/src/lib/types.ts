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
}
export interface HfGgufFile {
  filename: string;
  quant?: string;
  size_bytes?: number;
  size_gb?: number;
  fit: "fits" | "offload" | "too_large" | "unknown";
  vram_gb?: number;
  importable: boolean;
}
export interface HfGgufSearchResponse {
  query: string;
  total: number;
  system_vram?: number | null;
  ram_gb?: number | null;
  models: HfGgufModel[];
  error?: string;
}

export interface DownloadEntry {
  model?: string;
  status?: string;
  timestamp?: string;
  [k: string]: unknown;
}

export interface AptConfig {
  workspace: string;
  ollama_host: string;
  theme: string;
  default_model: string;
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
  model_weight_files_in_app: { path: string; size_bytes: number }[];
  models_are_bundled: boolean;
  model_install_mode: "on_demand_ollama_pull";
}
