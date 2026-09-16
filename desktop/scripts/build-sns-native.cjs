"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const repoRoot = path.resolve(__dirname, "..", "..");
const sourceRoot = path.join(repoRoot, "native", "wechat-sns-native");
const packageNativeDir = path.join(
  repoRoot,
  "src",
  "wechat_decrypt_tool",
  "native"
);
const adapterSource = path.join(
  sourceRoot,
  "adapters",
  "wechat_sns_adapters.jsonl"
);

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd || repoRoot,
    env: options.env || process.env,
    encoding: "utf8",
    stdio: options.stdio || "pipe",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const output = `${result.stdout || ""}${result.stderr || ""}`.trim();
    throw new Error(
      `${command} ${args.join(" ")} failed (${result.status})${
        output ? `:\n${output}` : ""
      }`
    );
  }
  return `${result.stdout || ""}${result.stderr || ""}`.trim();
}

function requireRegularFile(filePath, label) {
  let stat;
  try {
    stat = fs.statSync(filePath);
  } catch {
    throw new Error(`Missing ${label}: ${filePath}`);
  }
  if (!stat.isFile() || stat.size <= 0) {
    throw new Error(`Invalid ${label}: ${filePath}`);
  }
}

function expectedLibraryName(platform = process.platform) {
  if (platform === "darwin") return "libwechat_sns_native.dylib";
  if (platform === "win32") return "wechat_sns_native.dll";
  throw new Error(`wechat-sns-native packaging is unsupported on ${platform}`);
}

function resolvePrebuiltArtifact(env, platform) {
  const directoryValue = String(env.WCE_SNS_NATIVE_ARTIFACT_DIR || "").trim();
  if (!directoryValue) return null;
  const directory = path.resolve(directoryValue);
  const library = path.join(directory, expectedLibraryName(platform));
  const adapters = path.join(directory, "wechat_sns_adapters.jsonl");
  requireRegularFile(library, "prebuilt WeChat SNS native library");
  requireRegularFile(adapters, "prebuilt WeChat SNS adapter manifest");
  return { library, adapters };
}

function buildFromSource({ env, platform }) {
  if (platform === "darwin") {
    run("make", ["clean", "all"], { cwd: sourceRoot, env, stdio: "inherit" });
    return {
      library: path.join(sourceRoot, "build", expectedLibraryName(platform)),
      adapters: path.join(sourceRoot, "build", "wechat_sns_adapters.jsonl"),
    };
  }
  const buildDir = path.join(repoRoot, "desktop", "build", "wechat-sns-native");
  run(
    "cmake",
    ["-S", sourceRoot, "-B", buildDir, "-DCMAKE_BUILD_TYPE=Release"],
    { env, stdio: "inherit" }
  );
  run("cmake", ["--build", buildDir, "--config", "Release"], {
    env,
    stdio: "inherit",
  });
  const candidates = [
    path.join(buildDir, "Release", expectedLibraryName(platform)),
    path.join(buildDir, expectedLibraryName(platform)),
  ];
  const library = candidates.find((candidate) => fs.existsSync(candidate));
  if (!library) {
    throw new Error(
      `CMake completed without ${expectedLibraryName(platform)} in ${buildDir}`
    );
  }
  return { library, adapters: adapterSource };
}

function validateExports(library, platform) {
  const required = [
    "wcs_wechat_sns_abi_version",
    "wce_wechat_moments_sync_get_capability",
    "wce_wechat_moments_sync_begin",
    "wce_wechat_moments_sync_poll",
    "wce_wechat_moments_sync_cancel",
    "wce_wechat_moments_sync_close",
  ];
  let output;
  if (platform === "darwin") {
    output = run("nm", ["-gU", library]);
  } else {
    output = run("dumpbin", ["/exports", library]);
  }
  const missing = required.filter((name) => !output.includes(name));
  if (missing.length > 0) {
    throw new Error(
      `WeChat SNS native library is missing exports: ${missing.join(", ")}`
    );
  }
}

function buildSnsNativeCompanion({
  env = process.env,
  platform = process.platform,
  destinationDir = packageNativeDir,
} = {}) {
  const artifact =
    resolvePrebuiltArtifact(env, platform) || buildFromSource({ env, platform });
  requireRegularFile(artifact.library, "WeChat SNS native library");
  requireRegularFile(artifact.adapters, "WeChat SNS adapter manifest");
  validateExports(artifact.library, platform);
  fs.mkdirSync(destinationDir, { recursive: true });
  const libraryDestination = path.join(
    destinationDir,
    expectedLibraryName(platform)
  );
  const adapterDestination = path.join(
    destinationDir,
    "wechat_sns_adapters.jsonl"
  );
  fs.copyFileSync(artifact.library, libraryDestination);
  fs.copyFileSync(artifact.adapters, adapterDestination);
  return { library: libraryDestination, adapters: adapterDestination };
}

module.exports = {
  buildSnsNativeCompanion,
  expectedLibraryName,
};

if (require.main === module) {
  try {
    const result = buildSnsNativeCompanion();
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } catch (error) {
    process.stderr.write(`${error?.stack || error}\n`);
    process.exitCode = 1;
  }
}
