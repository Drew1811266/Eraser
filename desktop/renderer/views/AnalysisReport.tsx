// 分析报告卡片：告诉用户"找到了什么"，以及在动手之前还需要做什么（装检测器）。
import { useI18n } from '../i18n/useI18n';
import type { Analysis, DetectorEntry, WatermarkTrack } from '../../shared/ipc';
import type { InstallState } from '../store/analysis';

export type { InstallState };

interface AnalysisReportProps {
  analysis: Analysis | null;
  /** 编辑后的轨道：用户拆分/删除之后，报告必须跟着变，否则与时间线自相矛盾。 */
  tracks: WatermarkTrack[];
  detector: DetectorEntry | null;
  install: InstallState;
  onInstall: (modelId: string) => void;
}

const WARNING_KEYS: Record<string, string> = {
  no_watermark: 'analyze.warn.noWatermark',
  moving_watermark: 'analyze.warn.moving',
  multiple_watermarks: 'analyze.warn.multiple',
  low_confidence: 'analyze.warn.lowConfidence',
  quality_engine_unavailable: 'analyze.warn.qualityEngine',
};

export function AnalysisReport({ analysis, tracks, detector, install, onInstall }: AnalysisReportProps) {
  const { t } = useI18n();

  if (detector && !detector.available) {
    return (
      <section className="analysis-report analysis-report--blocked">
        <h3>{t('analyze.detectorMissing')}</h3>
        <p>{detector.blocked_reason || detector.display_name}</p>
        {install.phase === 'running' ? (
          <p>
            {t('analyze.installing')} {Math.round(install.progress * 100)}%
          </p>
        ) : (
          <button type="button" onClick={() => onInstall(detector.model_id)}>
            {install.phase === 'failed' ? t('analyze.retry') : t('analyze.install')}
          </button>
        )}
        {install.phase === 'failed' ? (
          <p role="alert">
            {t('analyze.installFailed')}: {install.error}
          </p>
        ) : null}
      </section>
    );
  }

  if (!analysis) {
    return null;
  }

  const watermarks = tracks;
  const intervalCount = watermarks.reduce((sum, track) => sum + track.intervals.length, 0);
  // The empty state already says "nothing found"; repeating it as a warning is
  // the same sentence twice on screen.
  const warnings = analysis.suggested.warnings.filter(
    (warning) => !(watermarks.length === 0 && warning === 'no_watermark'),
  );

  return (
    <section className="analysis-report">
      <h3>{t('analyze.title')}</h3>
      <dl className="analysis-report__stats">
        <div>
          <dt>{t('analyze.watermarks')}</dt>
          <dd>{watermarks.length}</dd>
        </div>
        <div>
          <dt>{t('analyze.shots')}</dt>
          <dd>{analysis.shots.length}</dd>
        </div>
        <div>
          <dt>{t('analyze.intervals')}</dt>
          <dd>{intervalCount}</dd>
        </div>
      </dl>

      {watermarks.length === 0 ? (
        <p className="analysis-report__empty">
          {t('analyze.none')} — {t('analyze.noneHint')}
        </p>
      ) : (
        <ul className="analysis-report__tracks">
          {watermarks.map((track, index) => (
            <li key={track.id}>
              <span>{`${t('analyze.watermarks')} ${index + 1}`}</span>
              <span>{track.motion === 'moving' ? t('analyze.moving') : t('analyze.static')}</span>
              <span>{`${track.intervals.length} ${t('analyze.segments')}`}</span>
            </li>
          ))}
        </ul>
      )}

      {warnings.length ? (
        <ul className="analysis-report__warnings">
          {warnings.map((warning) => (
            <li key={warning}>{t(WARNING_KEYS[warning] ?? warning)}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
