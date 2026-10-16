// Two separate AudioContexts: one at the Live API's input rate (16kHz, for
// mic capture) and one at its output rate (24kHz, for playback) -- this
// avoids resampling the agent's voice and keeps capture/playback decoupled.
const INPUT_SAMPLE_RATE = 16000;
const OUTPUT_SAMPLE_RATE = 24000;

const STATE_LABELS = {
  idle: 'Idle.',
  connecting: 'Connecting...',
  listening: 'Listening...',
  speaking: 'Priya is speaking...',
  checking: 'One moment, checking that...',
};

let captureContext = null;
let playerContext = null;
let micStream = null;
let micNode = null;
let playerNode = null;
let ws = null;
let callStartTime = null;
let timerInterval = null;

const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const micRing = document.getElementById('micRing');
const statusText = document.getElementById('statusText');
const timerEl = document.getElementById('timer');
const transcriptEl = document.getElementById('transcript');

startBtn.addEventListener('click', startCall);
stopBtn.addEventListener('click', stopCall);

function setState(state, customText) {
  micRing.className = `mic-ring ${state}`;
  statusText.textContent = customText || STATE_LABELS[state] || state;
}

function startTimer() {
  callStartTime = Date.now();
  timerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - callStartTime) / 1000);
    const mm = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const ss = String(elapsed % 60).padStart(2, '0');
    timerEl.textContent = `${mm}:${ss}`;
  }, 1000);
}

function stopTimer() {
  if (timerInterval) {
    clearInterval(timerInterval);
    timerInterval = null;
  }
}

function addTranscriptTurn(role, text) {
  const turn = document.createElement('div');
  turn.className = `turn ${role}`;
  const label = document.createElement('div');
  label.className = 'label';
  label.textContent = role === 'caller' ? 'You' : 'Priya';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  turn.appendChild(label);
  turn.appendChild(bubble);
  transcriptEl.appendChild(turn);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

async function startCall() {
  startBtn.disabled = true;
  transcriptEl.innerHTML = '';
  timerEl.textContent = '';
  setState('connecting', 'Requesting microphone access...');

  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    setState('idle', `Microphone access denied: ${err.message}`);
    startBtn.disabled = false;
    return;
  }

  captureContext = new AudioContext({ sampleRate: INPUT_SAMPLE_RATE });
  playerContext = new AudioContext({ sampleRate: OUTPUT_SAMPLE_RATE });
  await captureContext.audioWorklet.addModule('mic-worklet.js');
  await playerContext.audioWorklet.addModule('player-worklet.js');

  const micSource = captureContext.createMediaStreamSource(micStream);
  micNode = new AudioWorkletNode(captureContext, 'mic-capture-processor');
  micSource.connect(micNode);

  playerNode = new AudioWorkletNode(playerContext, 'player-processor');
  playerNode.connect(playerContext.destination);

  setState('connecting');
  const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${wsProtocol}://${window.location.host}/ws/call`);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    setState('listening');
    startTimer();
    micNode.port.onmessage = (event) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(event.data);
      }
    };
    stopBtn.disabled = false;
  };

  ws.onmessage = (event) => {
    if (typeof event.data === 'string') {
      const msg = JSON.parse(event.data);
      if (msg.type === 'interrupted') {
        playerNode.port.postMessage({ type: 'clear' });
        setState('listening');
      } else if (msg.type === 'state') {
        setState(msg.value);
      } else if (msg.type === 'transcript') {
        addTranscriptTurn(msg.role, msg.text);
      }
      return;
    }
    handlePlaybackAudio(event.data);
  };

  ws.onclose = () => {
    stopTimer();
    setState('idle', 'Call ended.');
    stopBtn.disabled = true;
    startBtn.disabled = false;
  };

  ws.onerror = () => {
    setState('idle', 'Connection error.');
  };
}

function handlePlaybackAudio(arrayBuffer) {
  const int16 = new Int16Array(arrayBuffer);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / 32768;
  }
  playerNode.port.postMessage({ type: 'push', samples: float32 });
}

function stopCall() {
  stopBtn.disabled = true;
  stopTimer();
  if (ws) {
    ws.close();
    ws = null;
  }
  if (micStream) {
    micStream.getTracks().forEach((track) => track.stop());
    micStream = null;
  }
  if (captureContext) {
    captureContext.close();
    captureContext = null;
  }
  if (playerContext) {
    playerContext.close();
    playerContext = null;
  }
  setState('idle');
  startBtn.disabled = false;
}
