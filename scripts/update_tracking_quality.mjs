import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import '../tracking-quality.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const data = path.join(root, 'data');
const destination = path.join(data, 'tracking_quality.json');
const tracking = JSON.parse(fs.readFileSync(path.join(data, 'strategy_tracking.json'), 'utf8'));
// A corrupt cohort must fail the update, never silently reset the denominator.
const previous = fs.existsSync(destination) ? JSON.parse(fs.readFileSync(destination, 'utf8')) : null;
const admissions = { ...(previous?.records || {}) };
const api = globalThis.TrackingQuality;
const marketDates = Object.fromEntries([['台股', 'latest.json'], ['美股', 'us_latest.json']].map(([market, filename]) => {
  const meta = JSON.parse(fs.readFileSync(path.join(data, filename), 'utf8')).meta || {};
  return [market, meta.officialDataDate || meta.completedDataDate || meta.strategyAsOfDate];
}));
for (const record of tracking.records || []) {
  if (admissions[record.trackingId]) continue;
  const currentDate = record.trackingCreatedDate || record.firstSignalDate;
  const firstDate = record.firstSignalDate;
  const hasCreationEvidence = (record.statusHistory || []).some((item) => item.date === firstDate && item.latestStatus)
    || record.latestStatus?.latestDataDate === firstDate;
  const forward = previous?.meta?.initializedAt && currentDate >= previous.meta.initializedAt.slice(0, 10)
    && firstDate === marketDates[record.market] && hasCreationEvidence;
  admissions[record.trackingId] = api.freezeAdmission(record, forward ? 'RECORDED_AT_CREATION' : 'HISTORICAL_REVIEW');
}
const now = new Date().toISOString();
const output = {
  meta: {
    version: api.VERSION,
    initializedAt: previous?.meta?.initializedAt || now,
    updatedAt: now,
    policy: '建立時固定資格；歷史回算另列，不以今日條件篩選過去輸贏。',
  },
  records: admissions,
};
fs.writeFileSync(`${destination}.tmp`, `${JSON.stringify(output, null, 2)}\n`);
fs.renameSync(`${destination}.tmp`, destination);
console.log(`Tracking quality: ${Object.keys(admissions).length} frozen admissions.`);
