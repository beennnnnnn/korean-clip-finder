import streamlit as st
import sqlite3
import pandas as pd
import requests
import re
import time
import os
from datetime import datetime
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs

# 페이지 설정
st.set_page_config(
    page_title="🎬 Korean Clip Finder",
    page_icon="🎬",
    layout="wide"
)

class YouTubeClipFinder:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.db_path = "user_captions.db"
        self.init_database()
    
    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS captions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                title TEXT,
                channel_name TEXT,
                speaker TEXT,
                text TEXT NOT NULL,
                start_time INTEGER NOT NULL,
                end_time INTEGER NOT NULL,
                duration REAL NOT NULL,
                language TEXT DEFAULT 'unknown',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(video_id, start_time)
            )
        ''')
        
        cur.execute('CREATE INDEX IF NOT EXISTS idx_speaker ON captions(speaker)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_text ON captions(text)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_video_id ON captions(video_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_language ON captions(language)')
        
        conn.commit()
        conn.close()
    
    def extract_video_id(self, url):
        if "youtube.com/watch" in url:
            return parse_qs(urlparse(url).query)['v'][0]
        elif "youtu.be/" in url:
            return url.split("/")[-1].split("?")[0]
        else:
            return url
    
    def get_video_info_oembed(self, video_id):
        try:
            url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            return {
                'title': data.get('title', 'Unknown Title'),
                'channel_name': data.get('author_name', 'Unknown Channel')
            }
        except:
            return {
                'title': f'Video {video_id}', 
                'channel_name': 'Unknown Channel'
            }
    
    def detect_language(self, text):
        korean_chars = len(re.findall(r'[가-힣]', text))
        english_chars = len(re.findall(r'[a-zA-Z]', text))
        japanese_chars = len(re.findall(r'[ひらがなカタカナ]|[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF]', text))
        total_chars = len(text.replace(' ', ''))
        
        if total_chars == 0:
            return 'unknown'
        
        korean_ratio = korean_chars / total_chars
        english_ratio = english_chars / total_chars
        japanese_ratio = japanese_chars / total_chars
        
        if korean_ratio > 0.3:
            return 'korean'
        elif japanese_ratio > 0.3:
            return 'japanese'
        elif english_ratio > 0.5:
            return 'english'
        else:
            return 'mixed'
    
    def detect_speaker(self, text, previous_speaker=None, language='unknown'):
        text = text.strip()
        
        korean_patterns = [
            r'^([가-힣]{2,4})\s*:',
            r'^\(([가-힣]{2,4})\)',
            r'^【([가-힣]{2,4})】'
        ]
        
        english_patterns = [
            r'^([A-Z][a-z]+ [A-Z][a-z]+)\s*:',
            r'^([A-Z][a-z]+)\s*:',
            r'^\(([A-Z][a-z]+)\)'
        ]
        
        japanese_patterns = [
            r'^([\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF]{2,8})\s*:',
            r'^\(([\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF]{2,8})\)'
        ]
        
        all_patterns = korean_patterns + english_patterns + japanese_patterns
        
        for pattern in all_patterns:
            match = re.match(pattern, text)
            if match:
                return match.group(1), re.sub(pattern, '', text).strip()
        
        default_speakers = {
            'korean': "화자",
            'english': "Speaker", 
            'japanese': "話者",
            'mixed': "Speaker"
        }
        
        return previous_speaker or default_speakers.get(language, "Speaker"), text
    
    def collect_subtitles(self, video_url):
        try:
            video_id = self.extract_video_id(video_url)
            
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            existing = cur.execute(
                "SELECT COUNT(*) FROM captions WHERE video_id = ?", 
                (video_id,)
            ).fetchone()[0]
            
            if existing > 0:
                conn.close()
                return f"이미 수집된 영상: {existing}개 자막 존재"
            
            try:
                transcript = YouTubeTranscriptApi.get_transcript(
                    video_id, 
                    languages=['ko', 'ko-KR', 'ja', 'ja-JP', 'en', 'en-US', 'en-GB', 'auto']
                )
            except Exception as e:
                conn.close()
                return f"자막 없음: {str(e)}"
            
            video_info = self.get_video_info_oembed(video_id)
            
            current_speaker = video_info['channel_name']
            saved_count = 0
            
            for item in transcript:
                text = item['text'].strip()
                if not text or len(text) < 2:
                    continue
                
                start_time = int(item['start'])
                duration = item['duration']
                end_time = int(start_time + duration)
                
                language = self.detect_language(text)
                detected_speaker, clean_text = self.detect_speaker(text, current_speaker, language)
                current_speaker = detected_speaker
                
                try:
                    cur.execute('''
                        INSERT OR IGNORE INTO captions 
                        (video_id, title, channel_name, speaker, text, start_time, end_time, duration, language)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        video_id, 
                        video_info['title'],
                        video_info['channel_name'],
                        detected_speaker,
                        clean_text,
                        start_time,
                        end_time,
                        duration,
                        language
                    ))
                    saved_count += 1
                except:
                    pass
            
            conn.commit()
            conn.close()
            
            return f"수집 완료: {saved_count}개 자막 저장"
            
        except Exception as e:
            return f"오류: {str(e)}"
    
    def search_captions(self, query, limit=50, language_filter=None):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        keywords = query.split()
        
        language_condition = ""
        if language_filter and language_filter != "all":
            language_condition = f" AND language = '{language_filter}'"
        
        if len(keywords) >= 2:
            speaker_keyword = keywords[0]
            text_keywords = keywords[1:]
            
            text_conditions = []
            params = [f"%{speaker_keyword}%"]
            
            for keyword in text_keywords:
                text_conditions.append("text LIKE ?")
                params.append(f"%{keyword}%")
            
            text_condition_str = " AND ".join(text_conditions)
            
            sql = f"""
                SELECT video_id, title, channel_name, speaker, text, start_time, end_time, duration, language
                FROM captions 
                WHERE speaker LIKE ?
                AND {text_condition_str}
                {language_condition}
                ORDER BY video_id, start_time
                LIMIT {limit}
            """
            
            results = cur.execute(sql, params).fetchall()
        else:
            sql = f"""
                SELECT video_id, title, channel_name, speaker, text, start_time, end_time, duration, language
                FROM captions 
                WHERE (text LIKE ? OR speaker LIKE ?)
                {language_condition}
                ORDER BY video_id, start_time
                LIMIT {limit}
            """
            keyword_pattern = f"%{query}%"
            results = cur.execute(sql, (keyword_pattern, keyword_pattern)).fetchall()
        
        conn.close()
        return results
    
    def get_stats(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        total_captions = cur.execute("SELECT COUNT(*) FROM captions").fetchone()[0]
        total_videos = cur.execute("SELECT COUNT(DISTINCT video_id) FROM captions").fetchone()[0]
        total_speakers = cur.execute("SELECT COUNT(DISTINCT speaker) FROM captions").fetchone()[0]
        
        lang_stats = cur.execute("""
            SELECT language, COUNT(*) as count 
            FROM captions 
            GROUP BY language 
            ORDER BY count DESC
        """).fetchall()
        
        conn.close()
        
        return {
            'total_captions': total_captions,
            'total_videos': total_videos,
            'total_speakers': total_speakers,
            'languages': dict(lang_stats)
        }

# 세션 상태 초기화
if 'finder' not in st.session_state:
    st.session_state.finder = YouTubeClipFinder()

# 메인 헤더
st.title("🎬 Korean Clip Finder")
st.markdown("한국어/영어/일본어 학습 & 영상 제작을 위한 유튜브 클립 검색기")

# 사이드바
st.sidebar.title("⚙️ 설정")

# API 키 입력
api_key = st.sidebar.text_input(
    "YouTube API 키 (선택사항)", 
    type="password",
    help="API 키가 있으면 더 정확한 정보를 가져올 수 있습니다"
)

# 자막 수집
st.sidebar.subheader("📥 자막 수집")
video_urls = st.sidebar.text_area(
    "YouTube URL (한 줄당 하나)",
    placeholder="https://www.youtube.com/watch?v=...",
    height=100
)

if st.sidebar.button("🚀 자막 수집 시작"):
    if video_urls.strip():
        urls = [url.strip() for url in video_urls.strip().split('\n') if url.strip()]
        
        for i, url in enumerate(urls):
            result = st.session_state.finder.collect_subtitles(url)
            st.sidebar.write(f"{i+1}. {result}")

# 통계
stats = st.session_state.finder.get_stats()
st.sidebar.subheader("📊 통계")
st.sidebar.write(f"자막: {stats['total_captions']:,}개")
st.sidebar.write(f"영상: {stats['total_videos']:,}개")
st.sidebar.write(f"화자: {stats['total_speakers']:,}명")

# 검색 인터페이스
st.subheader("🔍 검색")

col1, col2 = st.columns([3, 1])
with col1:
    search_query = st.text_input(
        "",
        placeholder="예: 유재석 정말, Trump great, 田中 面白い"
    )

with col2:
    language_filter = st.selectbox(
        "언어",
        ["all", "korean", "english", "japanese", "mixed"],
        format_func=lambda x: {
            "all": "전체", 
            "korean": "한국어", 
            "english": "영어", 
            "japanese": "일본어",
            "mixed": "혼합"
        }[x]
    )

if st.button("🔍 검색"):
    if search_query:
        results = st.session_state.finder.search_captions(
            search_query,
            language_filter=language_filter if language_filter != "all" else None
        )
        
        if results:
            st.success(f"🎯 {len(results)}개 결과 발견!")
            
            for result in results:
                video_id, title, channel, speaker, text, start_time, end_time, duration, language = result
                
                st.markdown(f"""
                **🎤 {speaker}**: {text}
                
                📺 {title} | 📻 {channel} | ⏱️ {start_time}~{end_time}초 | 🌐 {language}
                
                [▶️ YouTube에서 보기](https://www.youtube.com/watch?v={video_id}&t={start_time}s)
                
                ---
                """)
        else:
            st.warning("검색 결과가 없습니다.")