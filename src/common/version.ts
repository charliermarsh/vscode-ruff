/**
 * Describes a semantic version with major, minor, and patch components.
 */
export type VersionInfo = {
  major: number;
  minor: number;
  patch: number;
};

/**
 * Convert a version object to a string.
 */
export function versionToString(version: VersionInfo): string {
  return `${version.major}.${version.minor}.${version.patch}`;
}

/**
 * Check if version `a` is greater than or equal to version `b`.
 */
function versionGte(a: VersionInfo, b: VersionInfo): boolean {
  if (a.major > b.major) {
    return true;
  }
  if (a.major === b.major) {
    if (a.minor > b.minor) {
      return true;
    }
    if (a.minor === b.minor) {
      if (a.patch >= b.patch) {
        return true;
      }
    }
  }
  return false;
}

/**
 * The minimum version of the Ruff executable that supports the native server.
 */
export const MINIMUM_SUPPORTED_EXECUTABLE_VERSION: VersionInfo = { major: 0, minor: 1, patch: 0 };

/**
 * Check if the given version of the Ruff executable supports the native server.
 */
export function supportsExecutable(version: VersionInfo): boolean {
  return versionGte(version, MINIMUM_SUPPORTED_EXECUTABLE_VERSION);
}

/**
 * The minimum version of the Ruff executable that supports the native server.
 * TODO: remove this when dropped `ruff-lsp` and bumped `MINIMUM_SUPPORTED_EXECUTABLE_VERSION` to 0.3.5
 */
export const MINIMUM_NATIVE_SERVER_VERSION: VersionInfo = { major: 0, minor: 3, patch: 5 };

/**
 * Check if the given version of the Ruff executable supports the native server.
 */
export function supportsNativeServer(version: VersionInfo): boolean {
  return versionGte(version, MINIMUM_NATIVE_SERVER_VERSION);
}

/**
 * The version of the Ruff executable that made the native server stable.
 * TODO: remove this when bumped `MINIMUM_SUPPORTED_EXECUTABLE_VERSION` to 0.5.3
 */
export const NATIVE_SERVER_STABLE_VERSION: VersionInfo = { major: 0, minor: 5, patch: 3 };

/**
 * Check if the given version of the Ruff executable has the stable version of
 * the native server.
 */
export function supportsStableNativeServer(version: VersionInfo): boolean {
  return versionGte(version, NATIVE_SERVER_STABLE_VERSION);
}
