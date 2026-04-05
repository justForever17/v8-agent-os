export type DesktopLiveBridgeInjection =
    | { type: "start" }
    | { type: "answer"; sdp: string; sdpType: string }
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
      video {
        width: 100%;
        height: 100%;
        object-fit: contain;
        background: #000;
      }
    </style>
  </head>
  <body>
    <video id="desktopLiveVideo" autoplay playsinline muted></video>
    <script>
      (function () {
        var pc = null;
        var remoteStream = null;
        var starting = false;
        var video = document.getElementById("desktopLiveVideo");

        function post(type, payload) {
          try {
            window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify(Object.assign({ type: type }, payload || {})));
          } catch (error) {
            // noop
          }
        }

        async function teardown() {
          try {
            if (video) {
              try { video.pause(); } catch (error) {}
              video.srcObject = null;
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

        async function start() {
          if (starting) return;
          starting = true;
          await teardown();
          try {
            pc = new RTCPeerConnection({ iceServers: [] });
            remoteStream = new MediaStream();

            pc.addTransceiver("video", { direction: "recvonly" });

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
              if (video) {
                video.srcObject = stream;
                video.play().catch(function () {});
              }
              post("video-ready");
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

        window.__desktopLiveReceive = async function (payload) {
          try {
            if (!payload || typeof payload !== "object") return;
            if (payload.type === "start") {
              await start();
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
