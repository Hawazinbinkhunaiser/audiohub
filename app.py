import streamlit as st
import time
from datetime import timedelta
import xml.etree.ElementTree as ET
from xml.dom import minidom
import io
import os
from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs
import anthropic
import json

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
if 'scripts' not in st.session_state:
    st.session_state.scripts = {}
if 'audio_files' not in st.session_state:
    st.session_state.audio_files = {}
if 'sound_effects' not in st.session_state:
    st.session_state.sound_effects = {}

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

def generate_script_with_claude(section_info, api_key):
    """Generate audio tour script using Claude API"""
    try:
        client = anthropic.Anthropic(api_key=api_key)
        
        prompt = f"""Create an engaging audio tour script for the following section:

Section Title: {section_info['title']}
Duration: {section_info.get('duration', 'Not specified')} seconds
Additional Instructions: {section_info.get('instructions', 'Create an informative and engaging narration')}

Please create a natural-sounding script that:
1. Is appropriate for the given duration
2. Is engaging and informative
3. Uses conversational language suitable for audio narration
4. Includes natural pauses where appropriate (indicate with [pause])
5. Suggests sound effects where relevant (indicate with [SFX: description])

Format the response as JSON with the following structure:
{{
    "script": "The main narration text",
    "sound_effects": ["list of suggested sound effects with timestamps"],
    "estimated_word_count": number,
    "notes": "Any additional production notes"
}}"""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        # Parse the response
        response_text = message.content[0].text
        
        # Try to extract JSON from the response
        try:
            # Remove markdown code blocks if present
            if '```json' in response_text:
                response_text = response_text.split('```json')[1].split('```')[0].strip()
            elif '```' in response_text:
                response_text = response_text.split('```')[1].split('```')[0].strip()
            
            result = json.loads(response_text)
        except:
            # If JSON parsing fails, create a simple structure
            result = {
                "script": response_text,
                "sound_effects": [],
                "estimated_word_count": len(response_text.split()),
                "notes": "Script generated successfully"
            }
        
        return result
    except Exception as e:
        st.error(f"Error generating script: {str(e)}")
        return None

def generate_audio_with_elevenlabs(text, voice_id, api_key):
    """Generate audio using ElevenLabs API"""
    try:
        client = ElevenLabs(api_key=api_key)
        
        audio = client.text_to_speech.convert(
            voice_id=voice_id,
            output_format="mp3_44100_128",
            text=text,
            model_id="eleven_multilingual_v2",
            voice_settings=VoiceSettings(
                stability=0.5,
                similarity_boost=0.75,
                style=0.0,
                use_speaker_boost=True,
            ),
        )
        
        # Convert generator to bytes
        audio_bytes = b""
        for chunk in audio:
            audio_bytes += chunk
        
        return audio_bytes
    except Exception as e:
        st.error(f"Error generating audio: {str(e)}")
        return None

def get_elevenlabs_voices(api_key):
    """Fetch available voices from ElevenLabs"""
    try:
        client = ElevenLabs(api_key=api_key)
        voices = client.voices.get_all()
        return {voice.name: voice.voice_id for voice in voices.voices}
    except Exception as e:
        st.error(f"Error fetching voices: {str(e)}")
        return {}

def generate_resolve_xml(laps, fps=30):
    """Generate DaVinci Resolve compatible XML with markers"""
    # Create XML structure
    xmeml = ET.Element('xmeml', version='4')
    
    # Create sequence
    sequence = ET.SubElement(xmeml, 'sequence')
    ET.SubElement(sequence, 'name').text = 'Audio Tour Timeline'
    ET.SubElement(sequence, 'duration').text = str(timecode_to_frames(laps[-1]['end_time'], fps) if laps else 0)
    
    # Rate settings
    rate = ET.SubElement(sequence, 'rate')
    ET.SubElement(rate, 'timebase').text = str(fps)
    ET.SubElement(rate, 'ntsc').text = 'FALSE'
    
    # Timecode
    timecode = ET.SubElement(sequence, 'timecode')
    ET.SubElement(timecode, 'rate')
    rate_tc = timecode.find('rate')
    ET.SubElement(rate_tc, 'timebase').text = str(fps)
    ET.SubElement(rate_tc, 'ntsc').text = 'FALSE'
    ET.SubElement(timecode, 'string').text = '00:00:00:00'
    ET.SubElement(timecode, 'frame').text = '0'
    
    # Media
    media = ET.SubElement(sequence, 'media')
    
    # Video track
    video = ET.SubElement(media, 'video')
    track = ET.SubElement(video, 'track')
    
    # Add markers for each lap
    for i, lap in enumerate(laps):
        # Create a clip item for each section
        clipitem = ET.SubElement(track, 'clipitem', id=f"clipitem-{i+1}")
        ET.SubElement(clipitem, 'name').text = lap['title']
        ET.SubElement(clipitem, 'duration').text = str(timecode_to_frames(lap['duration'], fps))
        
        # Rate
        clip_rate = ET.SubElement(clipitem, 'rate')
        ET.SubElement(clip_rate, 'timebase').text = str(fps)
        ET.SubElement(clip_rate, 'ntsc').text = 'FALSE'
        
        # In/Out points
        ET.SubElement(clipitem, 'in').text = '0'
        ET.SubElement(clipitem, 'out').text = str(timecode_to_frames(lap['duration'], fps))
        ET.SubElement(clipitem, 'start').text = str(timecode_to_frames(lap['start_time'], fps))
        ET.SubElement(clipitem, 'end').text = str(timecode_to_frames(lap['end_time'], fps))
        
        # Add marker
        marker = ET.SubElement(clipitem, 'marker')
        ET.SubElement(marker, 'name').text = lap['title']
        ET.SubElement(marker, 'comment').text = f"Duration: {format_time(lap['duration'])}"
        ET.SubElement(marker, 'in').text = str(timecode_to_frames(lap['start_time'], fps))
        ET.SubElement(marker, 'out').text = str(timecode_to_frames(lap['end_time'], fps))
    
    # Pretty print XML
    xml_string = ET.tostring(xmeml, encoding='unicode')
    dom = minidom.parseString(xml_string)
    return dom.toprettyxml(indent='  ')

# Page config
st.set_page_config(
    page_title="Audio Tour Production Studio",
    page_icon="🎙️",
    layout="wide"
)

# Sidebar for API keys
with st.sidebar:
    st.header("🔑 API Configuration")
    
    claude_api_key = st.text_input(
        "Anthropic API Key",
        type="password",
        help="Get your API key from console.anthropic.com"
    )
    
    elevenlabs_api_key = st.text_input(
        "ElevenLabs API Key",
        type="password",
        help="Get your API key from elevenlabs.io"
    )
    
    st.divider()
    
    st.header("⚙️ Settings")
    production_mode = st.radio(
        "Mode",
        ["Timer Only", "Full Production"],
        help="Timer Only: Just track timestamps\nFull Production: Generate scripts and audio"
    )
    
    if production_mode == "Full Production" and elevenlabs_api_key:
        st.subheader("🎤 Voice Settings")
        voices = get_elevenlabs_voices(elevenlabs_api_key)
        if voices:
            selected_voice = st.selectbox(
                "Select Voice",
                options=list(voices.keys())
            )
            st.session_state.selected_voice_id = voices.get(selected_voice, "")
        else:
            st.warning("Enter valid API key to load voices")

# Title and description
st.title("🎙️ Audio Tour Production Studio")
st.markdown("""
Complete audio tour production toolkit: Create timestamps, generate AI scripts, 
convert to speech with ElevenLabs, and export to DaVinci Resolve.
""")

# Create tabs for different sections
tab1, tab2, tab3, tab4 = st.tabs(["⏱️ Timer & Timeline", "📝 Script Generation", "🎤 Audio Production", "📤 Export"])

with tab1:
    # Create two columns
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("⏱️ Timer Control")
        
        # Calculate current time
        if st.session_state.running and st.session_state.start_time:
            current_elapsed = st.session_state.elapsed_time + (time.time() - st.session_state.start_time)
        else:
            current_elapsed = st.session_state.elapsed_time
        
        # Display timer
        timer_display = st.empty()
        timer_display.markdown(f"## `{format_time(current_elapsed)}`")
        
        # Control buttons
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
                # Store the current time as lap end
                lap_end_time = current_elapsed
                
                # Create lap entry (title will be added below)
                st.session_state.laps.append({
                    'start_time': st.session_state.current_lap_start,
                    'end_time': lap_end_time,
                    'duration': lap_end_time - st.session_state.current_lap_start,
                    'title': f"Section {len(st.session_state.laps) + 1}"
                })
                
                # Update current lap start for next lap
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
            
            # Display and edit laps
            for i, lap in enumerate(st.session_state.laps):
                with st.expander(f"Section {i+1}: {lap['title']}", expanded=False):
                    # Edit title
                    new_title = st.text_input(
                        "Section Title",
                        value=lap['title'],
                        key=f"title_{i}"
                    )
                    st.session_state.laps[i]['title'] = new_title
                    
                    # Display times
                    st.write(f"**Start:** `{format_time(lap['start_time'])}`")
                    st.write(f"**End:** `{format_time(lap['end_time'])}`")
                    st.write(f"**Duration:** `{format_time(lap['duration'])}`")
                    
                    # Delete button
                    if st.button(f"🗑️ Delete Section {i+1}", key=f"delete_{i}"):
                        st.session_state.laps.pop(i)
                        st.rerun()
        else:
            st.info("No sections recorded yet. Start the timer and create your first lap!")

with tab2:
    st.subheader("🤖 AI Script Generation")
    
    if not claude_api_key:
        st.warning("⚠️ Please enter your Anthropic API key in the sidebar to use script generation.")
    elif not st.session_state.laps:
        st.info("📋 Create some timeline sections first in the Timer & Timeline tab.")
    else:
        st.markdown("Generate engaging audio tour scripts for each section using Claude AI.")
        
        # Select section to generate script for
        section_options = [f"Section {i+1}: {lap['title']}" for i, lap in enumerate(st.session_state.laps)]
        selected_section_idx = st.selectbox(
            "Select Section",
            range(len(section_options)),
            format_func=lambda x: section_options[x]
        )
        
        selected_lap = st.session_state.laps[selected_section_idx]
        
        col1, col2 = st.columns([2, 1])
        
        with col1:
            script_instructions = st.text_area(
                "Script Instructions",
                placeholder="E.g., Describe the architectural features of the main hall, mention the artist's background, highlight the historical significance...",
                height=150,
                key=f"instructions_{selected_section_idx}"
            )
        
        with col2:
            st.metric("Duration", f"{selected_lap['duration']:.1f}s")
            st.metric("Approx. Words", f"{int(selected_lap['duration'] * 2.5)}")
            st.caption("Based on ~150 words/minute")
        
        if st.button("✨ Generate Script", type="primary", use_container_width=True):
            with st.spinner("Generating script with Claude AI..."):
                section_info = {
                    'title': selected_lap['title'],
                    'duration': selected_lap['duration'],
                    'instructions': script_instructions
                }
                
                result = generate_script_with_claude(section_info, claude_api_key)
                
                if result:
                    st.session_state.scripts[selected_section_idx] = result
                    st.success("✅ Script generated successfully!")
                    st.rerun()
        
        # Display generated script
        if selected_section_idx in st.session_state.scripts:
            st.divider()
            script_data = st.session_state.scripts[selected_section_idx]
            
            st.subheader("Generated Script")
            
            # Editable script
            edited_script = st.text_area(
                "Script (editable)",
                value=script_data['script'],
                height=200,
                key=f"script_edit_{selected_section_idx}"
            )
            st.session_state.scripts[selected_section_idx]['script'] = edited_script
            
            # Sound effects suggestions
            if script_data.get('sound_effects'):
                with st.expander("🔊 Suggested Sound Effects"):
                    for sfx in script_data['sound_effects']:
                        st.write(f"• {sfx}")
            
            # Notes
            if script_data.get('notes'):
                with st.expander("📌 Production Notes"):
                    st.write(script_data['notes'])

with tab3:
    st.subheader("🎤 Audio Production")
    
    if not elevenlabs_api_key:
        st.warning("⚠️ Please enter your ElevenLabs API key in the sidebar to generate audio.")
    elif not st.session_state.scripts:
        st.info("📝 Generate scripts first in the Script Generation tab.")
    else:
        st.markdown("Convert your scripts to professional voice narration using ElevenLabs.")
        
        # Select section
        script_sections = [f"Section {i+1}: {st.session_state.laps[i]['title']}" 
                          for i in st.session_state.scripts.keys()]
        
        if script_sections:
            selected_idx = st.selectbox(
                "Select Section",
                list(st.session_state.scripts.keys()),
                format_func=lambda x: f"Section {x+1}: {st.session_state.laps[x]['title']}"
            )
            
            script_text = st.session_state.scripts[selected_idx]['script']
            
            # Show script preview
            with st.expander("📄 Script Preview", expanded=True):
                st.write(script_text)
            
            col1, col2 = st.columns(2)
            
            with col1:
                if st.button("🎙️ Generate Audio", type="primary", use_container_width=True):
                    if hasattr(st.session_state, 'selected_voice_id'):
                        with st.spinner("Generating audio with ElevenLabs..."):
                            audio_bytes = generate_audio_with_elevenlabs(
                                script_text,
                                st.session_state.selected_voice_id,
                                elevenlabs_api_key
                            )
                            
                            if audio_bytes:
                                st.session_state.audio_files[selected_idx] = audio_bytes
                                st.success("✅ Audio generated successfully!")
                                st.rerun()
                    else:
                        st.error("Please select a voice in the sidebar first.")
            
            # Display generated audio
            if selected_idx in st.session_state.audio_files:
                st.divider()
                st.subheader("Generated Audio")
                
                audio_data = st.session_state.audio_files[selected_idx]
                st.audio(audio_data, format='audio/mp3')
                
                col1, col2 = st.columns(2)
                with col1:
                    st.download_button(
                        label="⬇️ Download Audio",
                        data=audio_data,
                        file_name=f"section_{selected_idx+1}_{st.session_state.laps[selected_idx]['title'].replace(' ', '_')}.mp3",
                        mime="audio/mp3",
                        use_container_width=True
                    )
        else:
            st.info("No scripts available. Generate scripts in the Script Generation tab first.")

with tab4:
    st.subheader("📤 Export Options")
    
    if st.session_state.laps:
        export_col1, export_col2 = st.columns(2)
        
        with export_col1:
            fps = st.selectbox(
                "Frame Rate (FPS)",
                options=[24, 25, 30, 60],
                index=2,
                help="Select the frame rate for your DaVinci Resolve project"
            )
        
        with export_col2:
            st.write("")  # Spacing
            st.write("")  # Spacing
            
            # Generate XML
            xml_content = generate_resolve_xml(st.session_state.laps, fps)
            
            # Download button
            st.download_button(
                label="⬇️ Download XML Timeline",
                data=xml_content,
                file_name="audio_tour_timeline.xml",
                mime="application/xml",
                use_container_width=True,
                type="primary"
            )
        
        # Export all audio files as zip
        if st.session_state.audio_files:
            st.divider()
            st.subheader("📦 Batch Export")
            
            import zipfile
            from io import BytesIO
            
            if st.button("📦 Download All Audio Files (ZIP)", use_container_width=True):
                zip_buffer = BytesIO()
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for idx, audio_data in st.session_state.audio_files.items():
                        filename = f"section_{idx+1}_{st.session_state.laps[idx]['title'].replace(' ', '_')}.mp3"
                        zip_file.writestr(filename, audio_data)
                
                zip_buffer.seek(0)
                st.download_button(
                    label="⬇️ Download ZIP",
                    data=zip_buffer,
                    file_name="audio_tour_all_sections.zip",
                    mime="application/zip",
                    use_container_width=True
                )
        
        # Preview
        with st.expander("📄 Preview Timeline Summary"):
            st.markdown("### Timeline Overview")
            for i, lap in enumerate(st.session_state.laps):
                status_icons = []
                if i in st.session_state.scripts:
                    status_icons.append("📝")
                if i in st.session_state.audio_files:
                    status_icons.append("🎤")
                
                status = " ".join(status_icons) if status_icons else "⏱️"
                
                st.markdown(f"""
                **{i+1}. {lap['title']}** {status}
                - Start: `{format_time(lap['start_time'])}` (Frame: {timecode_to_frames(lap['start_time'], fps)})
                - End: `{format_time(lap['end_time'])}` (Frame: {timecode_to_frames(lap['end_time'], fps)})
                - Duration: `{format_time(lap['duration'])}`
                """)
    else:
        st.info("Record some sections to enable export options.")

# Auto-refresh for running timer
if st.session_state.running:
    time.sleep(0.1)
    st.rerun()
