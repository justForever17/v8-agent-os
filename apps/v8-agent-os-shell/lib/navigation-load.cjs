function isExpectedNavigationAbort(error) {
  const code = error?.code;
  const errno = Number(error?.errno);
  const message = String(error?.message || '');
  return code === 'ERR_ABORTED' || Number(code) === -3 || errno === -3 || /\bERR_ABORTED\b/.test(message);
}

async function loadUrlSafely(loadUrl, onUnexpectedError) {
  try {
    await loadUrl();
    return true;
  } catch (error) {
    if (!isExpectedNavigationAbort(error)) onUnexpectedError?.(error);
    return false;
  }
}

module.exports = {
  isExpectedNavigationAbort,
  loadUrlSafely,
};
