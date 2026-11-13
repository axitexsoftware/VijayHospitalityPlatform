(function(){
'use strict';
var open=document.getElementById('chat-open'),box=document.getElementById('chat'),log=document.getElementById('chat-log'),form=document.getElementById('chat-form'),inp=document.getElementById('chat-in'),language=document.getElementById('chat-language');
if(!open||!box||!form||!inp||!log)return;
var csrf=document.querySelector('meta[name=csrf]'),busy=false,started=false,historyReady=false,voiceReply=false,assistantName=box.dataset.assistantName||'VIRA',hotelName=box.dataset.hotelName||'Vijay Hospitality';
var voiceEnabled=box.dataset.voiceEnabled==='true',ttsEnabled=box.dataset.ttsEnabled==='true',defaultSpeechLang=box.dataset.speechLang||'en-IN';
var status=document.getElementById('chat-voice-status'),mic=document.getElementById('chat-mic'),speak=document.getElementById('chat-speak'),pause=document.getElementById('chat-pause'),stop=document.getElementById('chat-stop'),mute=document.getElementById('chat-mute');
var Recognition=window.SpeechRecognition||window.webkitSpeechRecognition,recognition=null,synth=window.speechSynthesis,lastSpoken='',muted=false;
try{var saved=localStorage.getItem('vijay-chat-language');if(language&&['auto','en','hi'].indexOf(saved)!==-1)language.value=saved}catch(e){}
function isHindiText(text){return /[\u0900-\u097f]/.test(text||'')}
function isHindi(){return language&&language.value==='hi'}
function words(en,hi){return isHindi()?hi:en}
function setStatus(text,state){if(!status)return;status.textContent=text;status.dataset.state=state||''}
function setMessage(item,text,createdAt){
 item.replaceChildren();var content=document.createElement('span');content.className='cm-text';content.textContent=text;item.appendChild(content);
 var time=document.createElement('time');time.className='cm-time';time.textContent=createdAt?createdAt.slice(11,16):new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});item.appendChild(time)
 if(item.classList.contains('b')&&text){lastSpoken=text;if(speak)speak.disabled=!ttsEnabled}
}
function add(text,kind,createdAt){
 var item=document.createElement('div');item.className='cm '+kind;setMessage(item,text,createdAt);log.appendChild(item);log.scrollTop=log.scrollHeight;
 if(kind==='b'&&text){lastSpoken=text;if(speak)speak.disabled=!ttsEnabled}
 return item
}
function welcome(){
 if(started)return;started=true;
 var en='Namaste! Welcome to '+hotelName+'. I am '+assistantName+', your virtual receptionist. I can help check live room availability and guide you through a Pay at Hotel reservation. How may I help you?';
 var hi='नमस्ते! '+hotelName+' में आपका स्वागत है। मैं '+assistantName+', आपकी वर्चुअल रिसेप्शनिस्ट हूँ। मैं कमरे की उपलब्धता देखने और Pay at Hotel बुकिंग में मदद कर सकती हूँ। मैं आपकी कैसे सहायता करूँ?';
 add(words(en,hi),'b')
}
function loadHistory(){
 fetch('/api/chat').then(function(response){if(!response.ok)throw new Error('history unavailable');return response.json()})
 .then(function(data){(data.messages||[]).forEach(function(message){add(message.content,message.role==='user'?'u':'b',message.created_at)});if(data.messages&&data.messages.length)started=true})
 .catch(function(){setStatus(words('Past messages could not be restored. You can still start a conversation.','पुराने संदेश नहीं लौट सके। आप नई बातचीत शुरू कर सकते हैं।'),'error')})
 .finally(function(){historyReady=true;if(box.classList.contains('open'))welcome();inp.disabled=false;log.scrollTop=log.scrollHeight})
}
function getSpeechLang(text){
 if(language&&language.value==='hi')return 'hi-IN';
 if(language&&language.value==='en')return 'en-IN';
 if(isHindiText(text))return 'hi-IN';
 if(navigator.language&&navigator.language.toLowerCase().indexOf('hi')===0)return 'hi-IN';
 return defaultSpeechLang
}
function updateLanguageUI(){
 inp.placeholder=words('Type your message','अपना संदेश लिखें');inp.setAttribute('aria-label',words('Your message','आपका संदेश'));
 open.setAttribute('aria-label',words('Chat with '+assistantName,assistantName+' से बात करें'));
 var launcher=open.querySelector('[data-chat-open-label]');if(launcher)launcher.textContent=words('Chat with '+assistantName,assistantName+' से बात करें');
 var title=document.querySelector('[data-chat-title]'),subtitle=document.querySelector('[data-chat-subtitle]'),languageLabel=document.querySelector('[data-chat-language-label]');
 if(title)title.textContent=assistantName+' · '+hotelName;
 if(subtitle)subtitle.textContent=words('Your Personal Hotel Receptionist · English / हिंदी','आपकी व्यक्तिगत होटल रिसेप्शनिस्ट · English / हिंदी');
 if(languageLabel)languageLabel.textContent=words('Chat language','चैट की भाषा');
 form.querySelector('button').textContent=words('Send','भेजें');
 if(mic){mic.setAttribute('aria-label',words('Start voice input','आवाज़ से संदेश बोलें'));var label=mic.querySelector('[data-voice-mic-label]');if(label)label.textContent=words('Speak','बोलें')}
 document.querySelectorAll('[data-chat-prompt]').forEach(function(button){button.textContent=isHindi()?button.dataset.hi:button.dataset.en})
}
function toggle(){box.classList.toggle('open');open.setAttribute('aria-expanded',box.classList.contains('open'));if(box.classList.contains('open')){if(historyReady)welcome();inp.focus()}}
open.addEventListener('click',toggle);
var close=document.getElementById('chat-close');if(close)close.addEventListener('click',toggle);
document.querySelectorAll('[data-open-chat]').forEach(function(button){button.addEventListener('click',function(){if(!box.classList.contains('open'))toggle()})});
if(language)language.addEventListener('change',function(){try{localStorage.setItem('vijay-chat-language',language.value)}catch(e){}updateLanguageUI()});
document.querySelectorAll('[data-chat-prompt]').forEach(function(button){button.addEventListener('click',function(){if(busy)return;inp.value=isHindi()?button.dataset.hi:button.dataset.en;form.requestSubmit()})});
function secureSpeechContext(){return window.isSecureContext||location.hostname==='localhost'||location.hostname==='127.0.0.1'}
function initRecognition(){
 if(!voiceEnabled){if(mic)mic.disabled=true;setStatus(words('Voice input is disabled. Text chat is available.','आवाज़ इनपुट बंद है। टेक्स्ट चैट उपलब्ध है।'));return}
 if(!Recognition){if(mic)mic.disabled=true;setStatus(words('Voice input is not supported by this browser. Text chat is available.','इस ब्राउज़र में आवाज़ इनपुट उपलब्ध नहीं है। टेक्स्ट चैट उपलब्ध है।'));return}
 if(!secureSpeechContext()){if(mic)mic.disabled=true;setStatus(words('Voice input requires a secure page. Text chat is available.','आवाज़ इनपुट के लिए सुरक्षित पेज चाहिए। टेक्स्ट चैट उपलब्ध है।'));return}
 recognition=new Recognition();recognition.interimResults=false;recognition.continuous=false;recognition.maxAlternatives=1;
 recognition.onstart=function(){if(mic){mic.setAttribute('aria-pressed','true');mic.disabled=false}setStatus(words('Listening…','सुन रही हूँ…'),'listening')};
 recognition.onresult=function(event){
  var transcript=event.results&&event.results[0]&&event.results[0][0]?event.results[0][0].transcript.trim():'';
  if(!transcript){voiceReply=false;setStatus(words('No speech was recognised. Please try again or type your message.','आवाज़ समझ में नहीं आई। फिर बोलें या संदेश लिखें।'),'error');return}
  inp.value=transcript;setStatus(words('Recognised. Sending…','समझ गई। संदेश भेज रही हूँ…'),'');form.requestSubmit()
 };
 recognition.onerror=function(event){
  var errors={'not-allowed':words('Microphone permission was denied. Allow microphone access or use text chat.','माइक्रोफ़ोन की अनुमति नहीं मिली। अनुमति दें या टेक्स्ट चैट का उपयोग करें।'),'service-not-allowed':words('Speech recognition is blocked by this browser. Use text chat.','इस ब्राउज़र में आवाज़ पहचान उपलब्ध नहीं है। टेक्स्ट चैट का उपयोग करें।'),'audio-capture':words('No microphone was found. Use text chat instead.','माइक्रोफ़ोन नहीं मिला। टेक्स्ट चैट का उपयोग करें।'),'no-speech':words('No speech was recognised. Please try again or type your message.','आवाज़ समझ में नहीं आई। फिर बोलें या संदेश लिखें।'),'network':words('Speech recognition network error. Please try again or type your message.','आवाज़ पहचान में नेटवर्क समस्या है। फिर कोशिश करें या संदेश लिखें。'),'language-not-supported':words('This speech language is not supported. Choose another chat language or type your message.','यह आवाज़ की भाषा समर्थित नहीं है। दूसरी भाषा चुनें या संदेश लिखें।')};
  voiceReply=false;setStatus(errors[event.error]||words('Voice input failed. Please try again or type your message.','आवाज़ इनपुट विफल हुआ। फिर कोशिश करें या संदेश लिखें।'),'error')
 };
 recognition.onend=function(){if(mic)mic.setAttribute('aria-pressed','false');if(status&&status.dataset.state==='listening')setStatus(words('Ready for voice input.','आवाज़ के लिए तैयार।'),'')}
}
initRecognition();
if(mic)mic.addEventListener('click',function(){
 if(!voiceEnabled){setStatus(words('Voice input is disabled. Please type your message.','आवाज़ इनपुट बंद है। कृपया संदेश लिखें।'),'error');return}
 if(!Recognition||!secureSpeechContext()){setStatus(words('Voice input is not supported here. Use text chat or open this page in a secure browser.','यहाँ आवाज़ इनपुट उपलब्ध नहीं है। टेक्स्ट चैट या सुरक्षित ब्राउज़र का उपयोग करें।'),'error');return}
 if(!recognition)initRecognition();
 if(mic.getAttribute('aria-pressed')==='true'){recognition.stop();return}
 try{recognition.lang=getSpeechLang(lastSpoken||inp.value);voiceReply=true;recognition.start()}catch(e){voiceReply=false;setStatus(words('Voice input could not start. Check microphone permission or type your message.','आवाज़ इनपुट शुरू नहीं हुआ। माइक्रोफ़ोन अनुमति जाँचें या संदेश लिखें।'),'error')}
});
function playLatest(){
 if(!ttsEnabled){setStatus(words('Spoken responses are disabled. You can read the chat response.','बोले गए जवाब बंद हैं। चैट में जवाब पढ़ें।'),'error');return}
 if(!synth||!window.SpeechSynthesisUtterance){setStatus(words('Speech playback is not supported by this browser. You can read the chat response.','इस ब्राउज़र में आवाज़ चलाना समर्थित नहीं है। चैट में जवाब पढ़ें।'),'error');return}
 if(!lastSpoken){setStatus(words('There is no response to play yet.','चलाने के लिए अभी कोई जवाब नहीं है।'),'');return}
 synth.cancel();var utterance=new SpeechSynthesisUtterance(lastSpoken),locale=getSpeechLang(lastSpoken);
 utterance.lang=locale;utterance.volume=muted?0:1;
 var available=synth.getVoices(),voice=available.find(function(candidate){return candidate.lang.toLowerCase()===locale.toLowerCase()})||available.find(function(candidate){return candidate.lang.toLowerCase().indexOf(locale.slice(0,2).toLowerCase())===0});
 if(voice)utterance.voice=voice;
 utterance.onerror=function(){setStatus(words('Speech playback failed. You can read the chat response.','आवाज़ नहीं चल पाई। चैट में जवाब पढ़ें।'),'error')};
 utterance.onend=function(){if(!muted)setStatus(words('Response finished.','जवाब पूरा हुआ।'),'')};
 setStatus(muted?words('Speech muted.','आवाज़ बंद है।'):words('Speaking…','बोल रही हूँ…'),'');synth.speak(utterance)
}
if(speak){speak.disabled=!ttsEnabled;speak.addEventListener('click',playLatest)}
if(pause)pause.disabled=!ttsEnabled;if(stop)stop.disabled=!ttsEnabled;if(mute)mute.disabled=!ttsEnabled;
if(pause)pause.addEventListener('click',function(){if(!synth)return;if(synth.speaking&&!synth.paused){synth.pause();setStatus(words('Speech paused. Press again to resume.','आवाज़ रोक दी गई। जारी रखने के लिए फिर दबाएँ।'), '');pause.textContent='▶'}else if(synth.paused){synth.resume();setStatus(words('Speaking…','बोल रही हूँ…'),'');pause.textContent='Ⅱ'}});
if(stop)stop.addEventListener('click',function(){if(synth)synth.cancel();setStatus(words('Speech stopped.','आवाज़ बंद कर दी गई।'),'');if(pause)pause.textContent='Ⅱ'});
if(mute)mute.addEventListener('click',function(){muted=!muted;mute.setAttribute('aria-pressed',String(muted));mute.setAttribute('aria-label',muted?words('Unmute speech','आवाज़ चालू करें'):words('Mute speech','आवाज़ बंद करें'));mute.textContent=muted?'🔇':'🔊';if(muted&&synth)synth.cancel();setStatus(muted?words('Speech muted.','आवाज़ बंद है।'):words('Speech unmuted.','आवाज़ चालू है।'),'')});
updateLanguageUI();inp.disabled=true;loadHistory();
form.addEventListener('submit',function(event){
 event.preventDefault();var message=inp.value.trim();if(!message||busy)return;
 inp.value='';add(message,'u');busy=true;var typing=add(words('Thinking…','सोच रही हूँ…'),'b');var sendButton=form.querySelector('button');if(sendButton)sendButton.disabled=true;
 fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf?csrf.content:''},body:JSON.stringify({message:message,language:language?language.value:'auto'})})
 .then(function(response){return response.json().then(function(data){return{ok:response.ok,data:data}})})
 .then(function(result){
  var data=result.data||{},reply=data.reply||data.error||words('Sorry, something went wrong. Please try again.','क्षमा करें, कुछ समस्या हुई। कृपया फिर प्रयास करें।');
  setMessage(typing,reply);
  if(data.booking||data.link){var link=document.createElement('a'),target=data.booking?'/booking/'+encodeURIComponent(data.booking):data.link;if(typeof target==='string'&&target.charAt(0)==='/'){link.href=target;link.textContent=data.booking?words('View booking ','बुकिंग देखें ')+data.booking:words('Continue','आगे बढ़ें');link.style.display='block';link.style.marginTop='6px';typing.insertBefore(link,typing.querySelector('.cm-time'))}}
  if(!result.ok)setStatus(words('The request could not be completed. Please try again.','अनुरोध पूरा नहीं हुआ। फिर कोशिश करें।'),'error');else if(muted)setStatus(words('Speech muted.','आवाज़ बंद है।'),'')
 })
 .catch(function(){voiceReply=false;setMessage(typing,words('Connection problem. Please try again.','कनेक्शन में समस्या है। कृपया फिर प्रयास करें।'));setStatus(words('Connection problem. Text chat is still available.','कनेक्शन में समस्या है। टेक्स्ट चैट उपलब्ध है।'),'error')})
 .finally(function(){busy=false;if(sendButton)sendButton.disabled=false;log.scrollTop=log.scrollHeight;if(voiceReply){voiceReply=false;playLatest()}})
});
})();
