import sherpa_onnx,glob,wave,json,numpy as np
d=glob.glob('sherpa-onnx-sense-voice-*')[0]
r=sherpa_onnx.OfflineRecognizer.from_sense_voice(model=d+'/model.int8.onnx',tokens=d+'/tokens.txt',language='ja',use_itn=False,num_threads=4)
w=wave.open('in16k.wav');sr=w.getframerate()
a=np.frombuffer(w.readframes(w.getnframes()),dtype=np.int16).astype(np.float32)/32768
cfg=sherpa_onnx.VadModelConfig();cfg.silero_vad.model='silero_vad.onnx';cfg.silero_vad.min_silence_duration=0.25;cfg.silero_vad.min_speech_duration=0.1;cfg.silero_vad.max_speech_duration=20;cfg.sample_rate=sr
vad=sherpa_onnx.VoiceActivityDetector(cfg,buffer_size_in_seconds=300)
segs=[];ws=cfg.silero_vad.window_size
for i in range(0,len(a),ws):
    vad.accept_waveform(a[i:i+ws])
    while not vad.empty():
        segs.append((vad.front.start,np.array(vad.front.samples)));vad.pop()
vad.flush()
while not vad.empty(): segs.append((vad.front.start,np.array(vad.front.samples)));vad.pop()
out=[]
for st,smp in segs:
    s=r.create_stream();s.accept_waveform(sr,smp);r.decode_stream(s)
    t0=st/sr;res=s.result
    out.append({'start':round(t0,2),'end':round(t0+len(smp)/sr,2),'text':res.text,'tokens':res.tokens,'ts':[round(t0+x,2) for x in res.timestamps]})
json.dump(out,open('transcript.json','w'),ensure_ascii=False)
for i,o in enumerate(out): print(f"[{i}] {o['start']:.2f}-{o['end']:.2f} {o['text']}")
