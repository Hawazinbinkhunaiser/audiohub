import streamlit as st
import time
from datetime import timedelta
import xml.etree.ElementTree as ET
from xml.dom import minidom
import io
import os
from pathlib import Path
import json

# Audio recording and processing
try:
    from st_audiorec import st_audiorec
    AUDIO_RECORDER_AVAILABLE = True
except ImportError:
    AUDIO_RECORDER_AVAILABLE = False
    
import openai
from anthropic import Anthropic
from elevenlabs import ElevenLabs, VoiceSettings
import requests

# Initialize session state
if 'running' not in st.session_state:
    st.session_state.running = False
if 'start_time' not in st.session_state:
    st.session_state.start_time = None
if 'elapsed_time' not in st.session_state:
    st.session_state.elapsed_time = 0
if 'laps' not in st.session_state:
    st.session_state.laps = []
if 'current_lap_start' not in st.session_state:
    st.session_state.current_lap_start = 0
if 'audio_data' not in st.session_state:
    st.session_state.audio_data = None
if 'transcription' not in st.session_state:
    st.session_state.transcription = ""
if 'script' not in st.session_state:
    st.session_state.script = ""
if 'generated_audio' not in st.session_state:
    st.session_state.generated_audio = None
if 'music_request' not in st.session_state:
    st.session_state.music_request = ""
if 'sfx_requests' not in st.session_state:
    st.session_state.sfx_requests = []

def format_time(seconds):
    """Format seconds to HH:MM:SS.mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milliseconds = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"

def timecode_to_frames(seconds, fps=30):
    """Convert seconds to frame count"""
    return int(seconds * fps)

def transcribe_audio(audio_bytes, api_key):
    """Transcribe audio using OpenAI Whisper"""
    try:
        client = openai.OpenAI(api_key=api_key)
        
        # Save audio to temporary file
        with open("temp_audio.wav", "wb") as f:
            f.write(audio_bytes)
        
        # Transcribe
        with open("temp_audio.wav", "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text"
            )
        
        # Clean up
        os.remove("temp_audio.wav")
        
        return transcript
    except Exception as e:
        st.error(f"Transcription error: {str(e)}")
        return None

def generate_audio_tour_script(transcription, laps, api_key, model_choice="claude"):
    """Generate audio tour script using LLM"""
    try:
        # Prepare context about the tour sections
        sections_info = "\n".join([
            f"Section {i+1} ({lap['title']}): Duration {format_time(lap['duration'])}"
            for i, lap in enumerate(laps)
        ])
        
        prompt = f"""Based on the following brainstorming notes and tour structure, create a professional audio tour script.

BRAINSTORMING NOTES:
{transcription}

TOUR STRUCTURE:
{sections_info}

Please create:
1. A complete audio tour script with narration for each section
2. Suggestions for background music mood/style
3. Suggestions for sound effects for each section

Format the output as JSON with this structure:
{{
    "sections": [
        {{
            "section_number": 1,
            "title": "Section Title",
            "script": "Narration text...",
            "duration": "00:00:00.000",
            "music_mood": "calm, ambient",
            "sound_effects": ["footsteps", "door opening"]
        }}
    ],
    "overall_music_description": "Description for background music generation",
    "production_notes": "Additional notes"
}}
"""

        if model_choice == "claude":
            client = Anthropic(api_key=api_key)
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4000,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            response_text = message.content[0].text
        else:  # OpenAI
            client = openai.OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {"role": "system", "content": "You are a professional audio tour script writer."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )
            response_text = response.choices[0].message.content
        
        # Parse JSON response
        script_data = json.loads(response_text)
        return script_data
    
    except Exception as e:
        st.error(f"Script generation error: {str(e)}")
        return None

def generate_voice_audio(text, api_key, voice_id, model_id="eleven_turbo_v2_5"):
    """Generate audio using ElevenLabs API"""
    try:
        client = ElevenLabs(api_key=api_key)
        
        audio = client.text_to_speech.convert(
            voice_id=voice_id,
            model_id=model_id,
            text=text,
            voice_settings=VoiceSettings(
                stability=0.5,
                similarity_boost=0.75,
                style=0.0,
                use_speaker_boost=True
            )
        )
        
        # Convert generator to bytes
        audio_bytes = b""
        for chunk in audio:
            audio_bytes += chunk
        
        return audio_bytes
    
    except Exception as e:
        st.error(f"Voice generation error: {str(e)}")
        return None

def generate_sound_effect(description, api_key):
    """Generate sound effect using ElevenLabs API"""
    try:
        url = "https://api.elevenlabs.io/v1/sound-generation"
        
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json"
        }
        
        data = {
            "text": description,
            "duration_seconds": 5.0,
            "prompt_influence": 0.3
        }
        
        response = requests.post(url, json=data, headers=headers)
        
        if response.status_code == 200:
            return response.content
        else:
            st.warning(f"Sound effect generation failed: {response.text}")
            return None
    
    except Exception as e:
        st.error(f"Sound effect generation error: {str(e)}")
        return None

def create_suno_music_request(description):
    """Create a formatted request for Suno AI music generation"""
    request = f"""
SUNO AI MUSIC GENERATION REQUEST

Description: {description}

Instructions for Suno AI:
1. Visit: https://suno.ai
2. Use the following prompt to generate background music:

PROMPT:
{description}

Suggested Settings:
- Style: Instrumental, Ambient
- Duration: Extended (if available)
- Mood: Match the tour atmosphere

Please generate and download the music file, then add it to your audio tour project.
"""
    return request

def generate_resolve_xml(laps, fps=30):
    """Generate DaVinci Resolve compatible XML with markers"""
    xmeml = ET.Element('xmeml', version='4')
    
    sequence = ET.SubElement(xmeml, 'sequence')
    ET.SubElement(sequence, 'name').text = 'Audio Tour Timeline'
    ET.SubElement(sequence, 'duration').text = str(timecode_to_frames(laps[-1]['end_time'], fps) if laps else 0)
    
    rate = ET.SubElement(sequence, 'rate')
    ET.SubElement(rate, 'timebase').text = str(fps)
    ET.SubElement(rate, 'ntsc').text = 'FALSE'
    
    timecode = ET.SubElement(sequence, 'timecode')
    ET.SubElement(timecode, 'rate')
    rate_tc = timecode.find('rate')
    ET.SubElement(rate_tc, 'timebase').text = str(fps)
    ET.SubElement(rate_tc, 'ntsc').text = 'FALSE'
    ET.SubElement(timecode, 'string').text = '00:00:00:00'
    ET.SubElement(timecode, 'frame').text = '0'
    
    media = ET.SubElement(sequence, 'media')
    video = ET.SubElement(media, 'video')
    track = ET.SubElement(video, 'track')
    
    for i, lap in enumerate(laps):
        clipitem = ET.SubElement(track, 'clipitem', id=f"clipitem-{i+1}")
        ET.SubElement(clipitem, 'name').text = lap['title']
        ET.SubElement(clipitem, 'duration').text = str(timecode_to_frames(lap['duration'], fps))
        
        clip_rate = ET.SubElement(clipitem, 'rate')
        ET.SubElement(clip_rate, 'timebase').text = str(fps)
        ET.SubElement(clip_rate, 'ntsc').text = 'FALSE'
        
        ET.SubElement(clipitem, 'in').text = '0'
        ET.SubElement(clipitem, 'out').text = str(timecode_to_frames(lap['duration'], fps))
        ET.SubElement(clipitem, 'start').text = str(timecode_to_frames(lap['start_time'], fps))
        ET.SubElement(clipitem, 'end').text = str(timecode_to_frames(lap['end_time'], fps))
        
        marker = ET.SubElement(clipitem, 'marker')
        ET.SubElement(marker, 'name').text = lap['title']
        ET.SubElement(marker, 'comment').text = f"Duration: {format_time(lap['duration'])}"
        ET.SubElement(marker, 'in').text = str(timecode_to_frames(lap['start_time'], fps))
        ET.SubElement(marker, 'out').text = str(timecode_to_frames(lap['end_time'], fps))
    
    xml_string = ET.tostring(xmeml, encoding='unicode')
    dom = minidom.parseString(xml_string)
    return dom.toprettyxml(indent='  ')

# Page config
st.set_page_config(
    page_title="AI Audio Tour Creator",
    page_icon="🎙️",
    layout="wide"
)

# Sidebar for API Configuration
with st.sidebar:
    st.header("🔑 API Configuration")
    
    openai_key = st.text_input("OpenAI API Key", type="password", help="For Whisper transcription")
    anthropic_key = st.text_input("Anthropic API Key", type="password", help="For Claude script generation")
    elevenlabs_key = st.text_input("ElevenLabs API Key", type="password", help="For voice & SFX generation")
    
    st.divider()
    
    st.header("🎤 Voice Settings")
    voice_id = st.text_input(
        "ElevenLabs Voice ID",
        value="21m00Tcm4TlvDq8ikWAM",
        help="Default: Rachel voice"
    )
    
    st.divider()
    
    st.header("🤖 LLM Selection")
    llm_choice = st.selectbox(
        "Script Generator",
        ["claude", "openai"],
        help="Choose which LLM to use for script generation"
    )

# Main App
st.title("🎙️ AI Audio Tour Creator")
st.markdown("""
Complete audio tour creation workflow: Record → Transcribe → Generate Script → Synthesize Voice → Create Music & SFX
""")

# Tab Navigation
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "1️⃣ Record & Transcribe",
    "2️⃣ Timer & Sections",
    "3️⃣ Generate Script",
    "4️⃣ Create Audio",
    "5️⃣ Export & Download"
])

# TAB 1: Recording and Transcription
with tab1:
    st.header("🎤 Record Your Brainstorming")
    
    st.info("Record yourself describing the audio tour content, locations, and key points you want to cover.")
    
    audio_bytes = None
    
    # Try to use audio recorder if available
    if AUDIO_RECORDER_AVAILABLE:
        st.subheader("Record Audio")
        wav_audio_data = st_audiorec()
        
        if wav_audio_data is not None:
            # Display audio player
            st.audio(wav_audio_data, format='audio/wav')
            audio_bytes = wav_audio_data
            st.session_state.audio_data = audio_bytes
    else:
        st.warning("⚠️ Live audio recording not available. Please upload an audio file instead.")
    
    # Always provide file upload option
    st.markdown("---")
    st.subheader("Or Upload Audio File")
    uploaded_file = st.file_uploader(
        "Upload audio file (WAV, MP3, M4A)",
        type=['wav', 'mp3', 'm4a', 'ogg'],
        help="Upload a pre-recorded audio file for transcription"
    )
    
    if uploaded_file is not None:
        st.audio(uploaded_file)
        audio_bytes = uploaded_file.read()
        st.session_state.audio_data = audio_bytes
    
    # Transcription button
    if audio_bytes or st.session_state.audio_data:
        if st.button("📝 Transcribe Audio", type="primary"):
            if not openai_key:
                st.error("Please enter your OpenAI API Key in the sidebar")
            else:
                with st.spinner("Transcribing audio..."):
                    audio_to_transcribe = audio_bytes if audio_bytes else st.session_state.audio_data
                    transcription = transcribe_audio(audio_to_transcribe, openai_key)
                    if transcription:
                        st.session_state.transcription = transcription
                        st.success("Transcription complete!")
                        st.rerun()
    
    if st.session_state.transcription:
        st.subheader("📄 Transcription")
        transcription_text = st.text_area(
            "Edit transcription if needed:",
            value=st.session_state.transcription,
            height=300
        )
        st.session_state.transcription = transcription_text

# TAB 2: Timer and Sections
with tab2:
    st.header("⏱️ Time Your Tour Sections")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("Timer Control")
        
        if st.session_state.running and st.session_state.start_time:
            current_elapsed = st.session_state.elapsed_time + (time.time() - st.session_state.start_time)
        else:
            current_elapsed = st.session_state.elapsed_time
        
        timer_display = st.empty()
        timer_display.markdown(f"## `{format_time(current_elapsed)}`")
        
        button_col1, button_col2, button_col3 = st.columns(3)
        
        with button_col1:
            if not st.session_state.running:
                if st.button("▶️ Start", use_container_width=True, type="primary"):
                    st.session_state.running = True
                    st.session_state.start_time = time.time()
                    st.rerun()
            else:
                if st.button("⏸️ Pause", use_container_width=True):
                    st.session_state.running = False
                    st.session_state.elapsed_time += time.time() - st.session_state.start_time
                    st.session_state.start_time = None
                    st.rerun()
        
        with button_col2:
            if st.button("⏹️ Stop Lap", use_container_width=True, disabled=not st.session_state.running and st.session_state.elapsed_time == 0):
                lap_end_time = current_elapsed
                
                st.session_state.laps.append({
                    'start_time': st.session_state.current_lap_start,
                    'end_time': lap_end_time,
                    'duration': lap_end_time - st.session_state.current_lap_start,
                    'title': f"Section {len(st.session_state.laps) + 1}"
                })
                
                st.session_state.current_lap_start = lap_end_time
                st.rerun()
        
        with button_col3:
            if st.button("🔄 Reset All", use_container_width=True):
                st.session_state.running = False
                st.session_state.start_time = None
                st.session_state.elapsed_time = 0
                st.session_state.laps = []
                st.session_state.current_lap_start = 0
                st.rerun()
    
    with col2:
        st.subheader("📝 Section Details")
        
        if st.session_state.laps:
            st.info(f"**Total Sections:** {len(st.session_state.laps)}")
            
            for i, lap in enumerate(st.session_state.laps):
                with st.expander(f"Section {i+1}: {lap['title']}", expanded=False):
                    new_title = st.text_input(
                        "Section Title",
                        value=lap['title'],
                        key=f"title_{i}"
                    )
                    st.session_state.laps[i]['title'] = new_title
                    
                    st.write(f"**Start:** `{format_time(lap['start_time'])}`")
                    st.write(f"**End:** `{format_time(lap['end_time'])}`")
                    st.write(f"**Duration:** `{format_time(lap['duration'])}`")
                    
                    if st.button(f"🗑️ Delete Section {i+1}", key=f"delete_{i}"):
                        st.session_state.laps.pop(i)
                        st.rerun()
        else:
            st.info("No sections recorded yet. Start the timer and create your first lap!")

# TAB 3: Script Generation
with tab3:
    st.header("✍️ Generate Audio Tour Script")
    
    if not st.session_state.transcription:
        st.warning("Please record and transcribe your brainstorming notes first (Tab 1)")
    elif not st.session_state.laps:
        st.warning("Please create tour sections with the timer (Tab 2)")
    else:
        st.success(f"Ready to generate! You have transcription and {len(st.session_state.laps)} sections.")
        
        if st.button("🤖 Generate Script with AI", type="primary"):
            required_key = anthropic_key if llm_choice == "claude" else openai_key
            if not required_key:
                st.error(f"Please enter your {llm_choice.upper()} API Key in the sidebar")
            else:
                with st.spinner("Generating audio tour script..."):
                    script_data = generate_audio_tour_script(
                        st.session_state.transcription,
                        st.session_state.laps,
                        required_key,
                        llm_choice
                    )
                    
                    if script_data:
                        st.session_state.script = script_data
                        st.success("Script generated successfully!")
                        st.rerun()
        
        if st.session_state.script:
            st.subheader("📜 Generated Script")
            
            script_data = st.session_state.script
            
            # Overall music description
            if 'overall_music_description' in script_data:
                st.info(f"**Background Music:** {script_data['overall_music_description']}")
            
            # Display each section
            for section in script_data.get('sections', []):
                with st.expander(f"Section {section['section_number']}: {section['title']}", expanded=True):
                    st.markdown(f"**Script:**\n\n{section['script']}")
                    st.write(f"**Duration:** {section.get('duration', 'N/A')}")
                    st.write(f"**Music Mood:** {section.get('music_mood', 'N/A')}")
                    st.write(f"**Sound Effects:** {', '.join(section.get('sound_effects', []))}")
            
            # Production notes
            if 'production_notes' in script_data:
                st.info(f"**Production Notes:** {script_data['production_notes']}")

# TAB 4: Audio Creation
with tab4:
    st.header("🔊 Generate Voice & Sound Effects")
    
    if not st.session_state.script:
        st.warning("Please generate the script first (Tab 3)")
    else:
        script_data = st.session_state.script
        
        # Voice Generation
        st.subheader("🎙️ Voice Generation")
        
        if st.button("🎵 Generate Complete Voice Narration", type="primary"):
            if not elevenlabs_key:
                st.error("Please enter your ElevenLabs API Key in the sidebar")
            else:
                with st.spinner("Generating voice narration for all sections..."):
                    all_audio = []
                    
                    for section in script_data.get('sections', []):
                        st.info(f"Generating Section {section['section_number']}...")
                        audio_bytes = generate_voice_audio(
                            section['script'],
                            elevenlabs_key,
                            voice_id
                        )
                        
                        if audio_bytes:
                            all_audio.append({
                                'section': section['section_number'],
                                'title': section['title'],
                                'audio': audio_bytes
                            })
                    
                    st.session_state.generated_audio = all_audio
                    st.success(f"Generated narration for {len(all_audio)} sections!")
                    st.rerun()
        
        # Display generated audio
        if st.session_state.generated_audio:
            st.subheader("🎧 Generated Audio Preview")
            for audio_item in st.session_state.generated_audio:
                st.write(f"**Section {audio_item['section']}: {audio_item['title']}**")
                st.audio(audio_item['audio'], format="audio/mp3")
        
        st.divider()
        
        # Sound Effects Generation
        st.subheader("🔔 Sound Effects Generation")
        
        sfx_list = []
        for section in script_data.get('sections', []):
            for sfx in section.get('sound_effects', []):
                if sfx not in sfx_list:
                    sfx_list.append(sfx)
        
        if sfx_list:
            st.write("**Detected Sound Effects:**")
            selected_sfx = st.multiselect(
                "Select sound effects to generate:",
                sfx_list,
                default=sfx_list[:3]  # Select first 3 by default
            )
            
            if st.button("🎼 Generate Selected Sound Effects"):
                if not elevenlabs_key:
                    st.error("Please enter your ElevenLabs API Key in the sidebar")
                else:
                    with st.spinner("Generating sound effects..."):
                        generated_sfx = []
                        
                        for sfx in selected_sfx:
                            st.info(f"Generating: {sfx}...")
                            sfx_audio = generate_sound_effect(sfx, elevenlabs_key)
                            
                            if sfx_audio:
                                generated_sfx.append({
                                    'name': sfx,
                                    'audio': sfx_audio
                                })
                        
                        st.session_state.sfx_requests = generated_sfx
                        st.success(f"Generated {len(generated_sfx)} sound effects!")
                        st.rerun()
            
            # Display generated SFX
            if st.session_state.sfx_requests:
                st.write("**Generated Sound Effects:**")
                for sfx_item in st.session_state.sfx_requests:
                    st.write(f"**{sfx_item['name']}**")
                    st.audio(sfx_item['audio'], format="audio/mp3")
        
        st.divider()
        
        # Music Generation Request
        st.subheader("🎵 Background Music")
        
        if 'overall_music_description' in script_data:
            music_desc = script_data['overall_music_description']
            
            st.write("**Recommended Music Description:**")
            st.info(music_desc)
            
            if st.button("📋 Create Suno AI Music Request"):
                suno_request = create_suno_music_request(music_desc)
                st.session_state.music_request = suno_request
                st.success("Music generation request created!")
                st.rerun()

# TAB 5: Export and Download
with tab5:
    st.header("📦 Export Your Audio Tour")
    
    export_col1, export_col2 = st.columns(2)
    
    # XML Timeline Export
    with export_col1:
        st.subheader("🎬 DaVinci Resolve Timeline")
        
        if st.session_state.laps:
            fps = st.selectbox(
                "Frame Rate (FPS)",
                options=[24, 25, 30, 60],
                index=2
            )
            
            xml_content = generate_resolve_xml(st.session_state.laps, fps)
            
            st.download_button(
                label="⬇️ Download XML Timeline",
                data=xml_content,
                file_name="audio_tour_timeline.xml",
                mime="application/xml",
                use_container_width=True,
                type="primary"
            )
        else:
            st.info("Create sections in Tab 2 to export timeline")
    
    # Audio Export
    with export_col2:
        st.subheader("🎧 Audio Files")
        
        if st.session_state.generated_audio:
            for i, audio_item in enumerate(st.session_state.generated_audio):
                st.download_button(
                    label=f"⬇️ Section {audio_item['section']}: {audio_item['title']}",
                    data=audio_item['audio'],
                    file_name=f"section_{audio_item['section']}_{audio_item['title'].replace(' ', '_')}.mp3",
                    mime="audio/mp3",
                    use_container_width=True,
                    key=f"audio_download_{i}"
                )
        else:
            st.info("Generate audio in Tab 4 to download")
    
    st.divider()
    
    # Sound Effects Export
    if st.session_state.sfx_requests:
        st.subheader("🔔 Sound Effects")
        
        sfx_col1, sfx_col2 = st.columns(2)
        for i, sfx_item in enumerate(st.session_state.sfx_requests):
            col = sfx_col1 if i % 2 == 0 else sfx_col2
            with col:
                st.download_button(
                    label=f"⬇️ {sfx_item['name']}",
                    data=sfx_item['audio'],
                    file_name=f"sfx_{sfx_item['name'].replace(' ', '_')}.mp3",
                    mime="audio/mp3",
                    use_container_width=True,
                    key=f"sfx_download_{i}"
                )
    
    st.divider()
    
    # Suno Music Request
    if st.session_state.music_request:
        st.subheader("🎵 Suno AI Music Generation")
        
        st.text_area(
            "Copy this request and use it on Suno AI:",
            value=st.session_state.music_request,
            height=300
        )
        
        st.markdown("[🎵 Open Suno AI](https://suno.ai)")
    
    st.divider()
    
    # Complete Script Export
    if st.session_state.script:
        st.subheader("📄 Complete Script")
        
        script_json = json.dumps(st.session_state.script, indent=2)
        
        st.download_button(
            label="⬇️ Download Complete Script (JSON)",
            data=script_json,
            file_name="audio_tour_script.json",
            mime="application/json",
            use_container_width=True
        )

# Auto-refresh for running timer
if st.session_state.running:
    time.sleep(0.1)
    st.rerun()
