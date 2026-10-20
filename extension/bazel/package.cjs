// Stage only runtime files. Compilation is a declared Bazel dependency.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createVSIX } = require('@vscode/vsce');
const stage = fs.mkdtempSync(path.join(os.tmpdir(), 'knowledge-vsix-'));
const output = path.resolve(process.argv[2]);
const manifest = JSON.parse(fs.readFileSync('package.json', 'utf8'));
delete manifest.scripts;
delete manifest.devDependencies;
fs.writeFileSync(path.join(stage, 'package.json'), JSON.stringify(manifest, null, 2) + '\n');
fs.copyFileSync('README.md', path.join(stage, 'README.md'));
fs.cpSync('dist', path.join(stage, 'dist'), { recursive: true, dereference: true });
function normalize(directory) {
  for (const entry of fs.readdirSync(directory).sort()) {
    const file = path.join(directory, entry);
    if (fs.statSync(file).isDirectory()) normalize(file);
    else if (file.endsWith('.map')) fs.unlinkSync(file);
    else fs.utimesSync(file, new Date('1980-01-01T00:00:00Z'), new Date('1980-01-01T00:00:00Z'));
  }
}
normalize(stage);
function canonicalizeZip(file) {
  // VSCE stamps generated manifest buffers with wall-clock time. Canonicalize
  // ZIP metadata after packaging; payload bytes and CRCs remain unchanged.
  const zip = fs.readFileSync(file);
  const end = zip.length - 22; // VSCE emits no archive comment or ZIP64 records.
  if (zip.readUInt32LE(end) !== 0x06054b50 || zip.readUInt16LE(end + 20) !== 0) {
    throw new Error('Unsupported VSIX ZIP layout');
  }
  let cursor = zip.readUInt32LE(end + 16);
  for (let i = 0; i < zip.readUInt16LE(end + 10); i++) {
    if (zip.readUInt32LE(cursor) !== 0x02014b50) throw new Error('Invalid ZIP directory');
    const local = zip.readUInt32LE(cursor + 42);
    if (zip.readUInt32LE(local) !== 0x04034b50) throw new Error('Invalid ZIP entry');
    if (zip.readUInt16LE(cursor + 30) || zip.readUInt16LE(local + 28)) {
      throw new Error('Unexpected timestamp-bearing ZIP extra fields');
    }
    zip.writeUInt16LE(0, cursor + 12);
    zip.writeUInt16LE(0x2821, cursor + 14); // 2000-01-01, independent of timezone.
    zip.writeUInt16LE(0, local + 10);
    zip.writeUInt16LE(0x2821, local + 12);
    zip.writeUInt32LE(0x81a40000, cursor + 38); // regular file, mode 0644.
    cursor += 46 + zip.readUInt16LE(cursor + 28) + zip.readUInt16LE(cursor + 30) + zip.readUInt16LE(cursor + 32);
  }
  fs.writeFileSync(file, zip);
}
createVSIX({ cwd: stage, packagePath: output, dependencies: false,
  allowMissingRepository: true, skipLicense: true }).then(() => canonicalizeZip(output)).catch(error => {
  console.error(error); process.exitCode = 1;
}).finally(() => fs.rmSync(stage, { recursive: true, force: true }));
