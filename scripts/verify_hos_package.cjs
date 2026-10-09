// Verify the release ZIP with HOS's actual installer in disposable storage.
// Usage: node scripts/verify_hos_package.cjs /absolute/path/to/hos [--install-deps]
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {createRequire} = require('node:module');
const {execFileSync} = require('node:child_process');

const hos = path.resolve(process.argv[2] || '.');
const server = path.join(hos, 'apps/server');
const project = path.join(server, 'tsconfig.json');
if (!fs.existsSync(project)) throw new Error('Provide a HOS source checkout with dependencies installed.');
const hostRequire = createRequire(path.join(server, 'package.json'));
const runtime = fs.mkdtempSync(path.join(os.tmpdir(), 'byd-hos-verify-'));
const sourceRoot = path.resolve(__dirname, '..');
const manifest = JSON.parse(fs.readFileSync(path.join(sourceRoot, 'byd_vehicle.driver.json'), 'utf8'));
const artifact = path.join(sourceRoot, 'dist', `haptique-byd-vehicle-${manifest.version}.zip`);
const withDependencies = process.argv.includes('--install-deps');
process.env.HOS_RUNTIME_STORAGE_DIR = runtime;
process.env.JWT_SECRET_KEY = 'disposable-byd-installer-test';
process.chdir(server);
hostRequire('ts-node').register({project, transpileOnly: true});
const ts = hostRequire('typescript');
const config = ts.readConfigFile(project, ts.sys.readFile).config;
hostRequire('tsconfig-paths').register({baseUrl: server, paths: config.compilerOptions.paths});

(async () => {
  try {
    const installer = hostRequire('./src/drivers/integration-package-installer');
    const result = await installer.installDriverPackageZip({
      packagePath: artifact, installDeps: withDependencies,
    });
    assert.equal(result.key, 'BYD_VEHICLE');
    assert.equal(result.definition.version, manifest.version);
    assert.equal(result.definition.driverType, 'python');
    assert.equal(result.hasRequirements, true);
    assert.equal(result.requirementsInstalled, withDependencies);
    assert.equal(result.manifest.envMap.password, 'BYD_PASSWORD');
    assert.equal(result.manifest.haptiqueApp, undefined);
    const installed = result.definition.driverPath;
    assert.ok(installed.startsWith(runtime + path.sep));
    assert.deepEqual(fs.readFileSync(installed), fs.readFileSync(path.join(sourceRoot, 'byd_vehicle.py')));
    if (withDependencies) {
      const pythonRuntime = hostRequire('./src/utils/python-runtime');
      const venv = pythonRuntime.buildPythonScriptVenvDir(installed, result.key);
      const python = pythonRuntime.getPythonVenvPaths(venv).pythonPath;
      const checked = JSON.parse(execFileSync(python, [installed, '--check'], {encoding: 'utf8'}));
      assert.equal(checked.pybyd, '0.0.77');
      assert.equal(checked.protocolVersion, 1);
    }
    console.log(JSON.stringify({status: 'passed', installer: 'HOS source runtime',
      version: manifest.version, dependenciesInstalled: withDependencies, vehicleContacted: false}));
  } finally {
    fs.rmSync(runtime, {recursive: true, force: true});
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
