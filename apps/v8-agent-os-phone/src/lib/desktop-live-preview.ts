export type DesktopLiveBridgeInjection =
    | {
        type: "start";
        iceServers?: Array<{ urls?: string | string[]; username?: string; credential?: string }>;
        audioEnabled?: boolean;
    }
    | { type: "answer"; sdp: string; sdpType: string }
    | { type: "fallback-stream"; streamUrl: string }
    | { type: "close" };

export function buildDesktopLivePreviewHtml() {
    return `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta
      name="viewport"
      content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover"
    />
    <style>
      html, body {
        margin: 0;
        padding: 0;
        width: 100%;
        height: 100%;
        overflow: hidden;
        background: #000;
      }
      body {
        display: flex;
        align-items: center;
        justify-content: center;
      }
      video, img {
        width: 100%;
        height: 100%;
        object-fit: contain;
        background: #000;
      }
    </style>
  </head>
  <body>
    <video id="desktopLiveVideo" autoplay playsinline></video>
    <img id="desktopLiveFallback" alt="Desktop live fallback stream" style="display: none;" />
    <script>
      (function () {
        var pc = null;
        var remoteStream = null;
        var starting = false;
        var videoReadyTimer = null;
        var videoReadyPosted = false;
        var video = document.getElementById("desktopLiveVideo");
        var fallbackImage = document.getElementById("desktopLiveFallback");

        function post(type, payload) {
          try {
            window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify(Object.assign({ type: type }, payload || {})));
          } catch (error) {
            // noop
          }
        }

        async function teardown() {
          try {
            if (videoReadyTimer) {
              clearTimeout(videoReadyTimer);
              videoReadyTimer = null;
            }
            videoReadyPosted = false;
            if (video) {
              try { video.pause(); } catch (error) {}
              video.onloadeddata = null;
              video.onplaying = null;
              video.srcObject = null;
              video.style.display = "";
            }
            if (fallbackImage) {
              fallbackImage.removeAttribute("src");
              fallbackImage.style.display = "none";
            }
            if (remoteStream) {
              remoteStream.getTracks().forEach(function (track) {
                try { track.stop(); } catch (error) {}
              });
              remoteStream = null;
            }
            if (pc) {
              try { pc.close(); } catch (error) {}
              pc = null;
            }
          } catch (error) {
            // noop
          } finally {
            starting = false;
          }
        }

        function markVideoReady(payload) {
          if (videoReadyPosted) return;
          videoReadyPosted = true;
          if (videoReadyTimer) {
            clearTimeout(videoReadyTimer);
            videoReadyTimer = null;
          }
          post("video-ready", payload || {});
        }

        function armVideoReady() {
          if (!video) return;
          var maybeReady = function () {
            if (!video || videoReadyPosted) return;
            if (video.videoWidth > 0 || video.videoHeight > 0 || video.readyState >= 2) {
              markVideoReady();
            }
          };
          video.onloadeddata = maybeReady;
          video.onplaying = maybeReady;
          if (typeof video.requestVideoFrameCallback === "function") {
            try {
              video.requestVideoFrameCallback(function () {
                markVideoReady();
              });
            } catch (error) {
              // keep the normal media events as fallback
            }
          }
          setTimeout(maybeReady, 250);
        }

        async function start(payload) {
          if (starting) return;
          starting = true;
          await teardown();
          try {
            var iceServers = payload && Array.isArray(payload.iceServers) ? payload.iceServers : [];
            var audioEnabled = !!(payload && payload.audioEnabled);
            pc = new RTCPeerConnection({ iceServers: iceServers });
            remoteStream = new MediaStream();
            videoReadyTimer = setTimeout(function () {
              post("error", { message: "desktop-live-webrtc-video-timeout" });
            }, 8000);
            if (video) {
              video.muted = !audioEnabled;
            }

            pc.addTransceiver("video", { direction: "recvonly" });
            if (audioEnabled) {
              pc.addTransceiver("audio", { direction: "recvonly" });
            }

            pc.onicecandidate = function (event) {
              post("ice-candidate", {
                candidate: event.candidate ? event.candidate.toJSON() : null,
              });
            };

            pc.ontrack = function (event) {
              var stream = event.streams && event.streams[0] ? event.streams[0] : remoteStream;
              if ((!event.streams || event.streams.length === 0) && event.track && remoteStream) {
                remoteStream.addTrack(event.track);
              }
              if (video && event.track && event.track.kind === "video") {
                video.srcObject = stream;
                armVideoReady();
                video.play().then(function () {
                  armVideoReady();
                }).catch(function () {});
              }
              if (event.track && event.track.kind === "audio") {
                post("audio-ready");
              }
            };

            pc.onconnectionstatechange = function () {
              post("connection-state", { state: pc ? pc.connectionState : "closed" });
            };

            var offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            var localDescription = pc.localDescription || offer;
            if (!localDescription || !localDescription.sdp) {
              throw new Error("local-offer-unavailable");
            }
            post("local-offer", {
              sdp: localDescription.sdp,
              offerType: localDescription.type || "offer",
            });
          } catch (error) {
            post("error", { message: error && error.message ? error.message : String(error) });
          } finally {
            starting = false;
          }
        }

        async function showFallbackStream(streamUrl) {
          await teardown();
          if (!streamUrl) {
            throw new Error("fallback-stream-unavailable");
          }
          if (video) {
            try { video.pause(); } catch (error) {}
            video.srcObject = null;
            video.style.display = "none";
          }
          if (fallbackImage) {
            fallbackImage.style.display = "";
            fallbackImage.src = streamUrl;
          }
          markVideoReady({ fallback: true });
        }

        window.__desktopLiveReceive = async function (payload) {
          try {
            if (!payload || typeof payload !== "object") return;
            if (payload.type === "start") {
              await start(payload);
              return;
            }
            if (payload.type === "answer") {
              if (!pc) return;
              await pc.setRemoteDescription({
                type: payload.sdpType || "answer",
                sdp: payload.sdp || "",
              });
              return;
            }
            if (payload.type === "fallback-stream") {
              await showFallbackStream(payload.streamUrl || "");
              return;
            }
            if (payload.type === "close") {
              await teardown();
            }
          } catch (error) {
            post("error", { message: error && error.message ? error.message : String(error) });
          }
        };

        post("ready");
      })();
    </script>
  </body>
</html>`;
}

export function buildDesktopLiveBridgeInjection(payload: DesktopLiveBridgeInjection) {
    return `window.__desktopLiveReceive(${JSON.stringify(payload)}); true;`;
}
