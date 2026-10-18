import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';

const page = await readFile('output/garblo-solution.html', 'utf8');
const expected = [
  'architecture-tab', 'database-tab', 'role="tablist"',
  'aria-controls="architecture-panel"', 'aria-controls="database-panel"',
  'users', 'consent_records', 'media_assets', 'garments', 'lookbooks',
  'looks', 'look_items', 'ai_jobs', 'approval_requests', 'style_signals',
  'taste_matches', 'media_assets.object_key', 'looks.generated_asset_id'
];
const missing = expected.filter((value) => !page.includes(value));
const match = page.match(/<script id="architecture-source" type="application\/octet-stream">([A-Za-z0-9+/=]+)<\/script>/);
if (!match) throw new Error('The embedded Archify artifact is missing.');
const databaseMatch = page.match(/<script id="database-source" type="application\/octet-stream">([A-Za-z0-9+/=]+)<\/script>/);
if (!databaseMatch) throw new Error('The embedded Archify database relation artifact is missing.');

const embedded = Buffer.from(match[1], 'base64');
const artifact = await readFile('output/garblo-architecture.html');
const embeddedDatabase = Buffer.from(databaseMatch[1], 'base64');
const databaseArtifact = await readFile('output/garblo-database-relation.html');
const digest = (value) => createHash('sha256').update(value).digest('hex');
const embeddedArtifactMatches = digest(embedded) === digest(artifact);
const embeddedDatabaseMatches = digest(embeddedDatabase) === digest(databaseArtifact);

console.log(JSON.stringify({ missing, embeddedArtifactMatches, embeddedDatabaseMatches, architectureBytes: embedded.length, databaseBytes: embeddedDatabase.length, wrapperBytes: Buffer.byteLength(page) }));
if (missing.length || !embeddedArtifactMatches || !embeddedDatabaseMatches) process.exitCode = 1;
