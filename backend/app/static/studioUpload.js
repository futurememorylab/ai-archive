// Send one file into a Prompt Studio uploaded set (used by uploadClips in
// studio.js). In the cloud the bytes go straight to the media bucket through
// a signed URL, because Cloud Run refuses requests over 32 MB; the app only
// sees the start and the finish. Local runs answer {direct: false} and the
// file goes through the app as before. See services/direct_upload.py.
(function () {
  async function errorDetail(res) {
    try { return (await res.json()).detail || `HTTP ${res.status}`; }
    catch (e) { return `HTTP ${res.status}`; }
  }

  async function postForm(url, fd) {
    const res = await fetch(url, { method: 'POST', headers: { 'HX-Request': 'true' }, body: fd });
    if (!res.ok) throw new Error(await errorDetail(res));
  }

  function withPoster(fd, poster, setId) {
    fd.append('set_id', String(setId));
    if (poster) fd.append('poster', poster, 'poster.jpg');
    return fd;
  }

  function viaApp(file, meta, poster, setId) {
    const fd = withPoster(new FormData(), poster, setId);
    fd.append('file', file, file.name);
    if (meta.duration != null) fd.append('duration_secs', String(meta.duration));
    if (meta.width != null) fd.append('width', String(meta.width));
    if (meta.height != null) fd.append('height', String(meta.height));
    return postForm('/api/studio/uploads', fd);
  }

  // XHR rather than fetch: it reports upload progress, which a large clip needs.
  function put(url, file, mime, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', url);
      xhr.setRequestHeader('Content-Type', mime);
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      xhr.onload = () => (xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error(`storage refused the file (HTTP ${xhr.status})`)));
      xhr.onerror = () => reject(new Error('the connection to storage failed'));
      xhr.send(file);
    });
  }

  async function direct(start, file, poster, setId, onProgress) {
    const base = `/api/studio/uploads/direct/${start.clip_id}`;
    try {
      await put(start.upload_url, file, start.mime, onProgress);
    } catch (err) {
      // Free the reserved clip; the caller reports the upload error itself.
      fetch(base, { method: 'DELETE' }).catch((e) => console.warn('upload abort failed', e));
      throw err;
    }
    await postForm(`${base}/complete`, withPoster(new FormData(), poster, setId));
  }

  async function send(file, meta, poster, setId, onProgress) {
    const res = await fetch('/api/studio/uploads/direct', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filename: file.name, mime: file.type, size_bytes: file.size,
        duration_secs: meta.duration, width: meta.width, height: meta.height,
      }),
    });
    if (!res.ok) throw new Error(await errorDetail(res));
    const start = await res.json();
    if (!start.direct) return viaApp(file, meta, poster, setId);
    return direct(start, file, poster, setId, onProgress);
  }

  window.studioUpload = { send };
})();
