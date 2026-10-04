export const PipelineStage = {
  RECEIVED: "received",
  QUEUED: "queued",
  PROCESSING: "processing",
  RETRYING: "retrying",
  DEAD_LETTERED: "dead_lettered",
  SUCCESS: "success",
} as const;

export type PipelineStageValue = (typeof PipelineStage)[keyof typeof PipelineStage];

export const PIPELINE_STAGE_ORDER: PipelineStageValue[] = [
  PipelineStage.RECEIVED,
  PipelineStage.QUEUED,
  PipelineStage.PROCESSING,
  PipelineStage.SUCCESS,
  PipelineStage.DEAD_LETTERED,
];