import { getModelMeta, isCloudModel } from './constants';
import type { CloudModel, ModelConfig } from './types';

export function cloudModelDisplayLabel(model: CloudModel): string {
  return model.label;
}

export function modelDisplayLabel(modelId: string, cloudModels: CloudModel[]): string {
  const registry = cloudModels.find((item) => item.id === modelId);
  if (registry) {
    return cloudModelDisplayLabel(registry);
  }
  return getModelMeta(modelId).label;
}

export function modelConfigDisplayName(config: ModelConfig): string {
  if (config.backend === 'Cloud API') {
    return config.name;
  }
  return getModelMeta(config.id).label;
}

export function modelStatusDotClass(config: ModelConfig): string {
  return modelIdStatusDotClass(config.id, config.backend === 'Cloud API');
}

export function modelIdStatusDotClass(modelId: string, isCloud = isCloudModel(modelId)): string {
  if (isCloud) {
    return 'bg-amber-500';
  }
  const meta = getModelMeta(modelId);
  if (meta.accent === '#7C3AED' || modelId.includes('gemma')) {
    return 'bg-violet-500';
  }
  return 'bg-emerald-500';
}
