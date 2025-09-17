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
    layout="wide",
    initial_sidebar_state="expanded"
)

# 스타일링
st.markdown("""
<style>
.main-header {
    text-align: center;
    padding: 2rem 0;
    background: linear-gradient(90deg, #4CAF50, #45a049);
    color: white;
    border-radius: 10px;
    margin-bottom: 2rem;
}
.search-box {
    background: #f8f9fa;
    padding: 1.5rem;
    border-radius: 10px;
    border-left: 4px solid #4CAF50;
    margin: 1rem 0;
}
.result-item {
    background: white;
    padding: 1rem;
    border-radius: 8px;
    border: 1px solid #ddd;
    margin: 0.5rem 0;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}
</style>
""", unsafe_allow_html=True)

class YouTubeClipFinder:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.db_path = "user_captions.db"
        self.init_database()
    
    def init_database(self):
        """데이터베이스 초기화"""
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
        
        # 검색 성능을 위한 인덱스
        cur.execute('CREATE INDEX IF NOT EXISTS idx_speaker ON captions(speaker)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_text ON captions(text)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_video_id ON captions(video_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_language ON captions(language)')
        
        conn.commit()
        conn.close()
    
    def extract_video_id(self, url):
        """YouTube URL에서 비디오 ID 추출"""
        if "youtube.com/watch" in url:
            return parse_qs(urlparse(url).query)['v'][0]
        elif "youtu.be/" in url:
            return url.split("/")[-1].split("?")[0]
        else:
            return url
    
    def get_video_info_api(self, video_id):
        """YouTube Data API로 비디오 정보 가져오기"""
        if not self.api_key:
            return self.get_video_info_oembed(video_id)
        
        try:
            url = f"https://www.googleapis.com/youtube/v3/videos"
            params = {
                'part': 'snippet',
                'id': video_id,
                'key': self.api_key
            }
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data['items']:
                    snippet = data['items'][0]['snippet']
                    return {
                        'title': snippet.get('title', 'Unknown Title'),
                        'channel_name': snippet.get('channelTitle', 'Unknown Channel'),
                        'description': snippet.get('description', ''),
                        'published_at': snippet.get('publishedAt', '')
                    }
        except Exception as e:
            st.warning(f"API 오류, oEmbed로 대체: {e}")
            return self.get_video_info_oembed(video_id)
        
        return {'title': 'Unknown Title', 'channel_name': 'Unknown Channel'}
    
    def get_video_info_oembed(self, video_id):
        """oEmbed API로 비디오 정보 가져오기 (API 키 없을 때)"""
        try:
            url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                return {
                    'title': data.get('title', 'Unknown Title'),
                    'channel_name': data.get('author_name', 'Unknown Channel')
                }
        except:
            pass
        
        return {'title': 'Unknown Title', 'channel_name': 'Unknown Channel'}
    
    def detect_language(self, text):
        """텍스트 언어 감지 (한국어, 영어, 일본어 지원)"""
        korean_chars = len(re.findall(r'[가-힣]', text))
        english_chars = len(re.findall(r'[a-zA-Z]', text))
        japanese_chars = len(re.findall(r'[ひらがなカタカナ漢字]|[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF]', text))
        total_chars = len(text.replace(' ', ''))
        
        if total_chars == 0:
            return 'unknown'
        
        korean_ratio = korean_chars / total_chars
        english_ratio = english_chars / total_chars
        japanese_ratio = japanese_chars / total_chars
        
        # 가장 높은 비율의 언어 선택
        ratios = {
            'korean': korean_ratio,
            'english': english_ratio, 
            'japanese': japanese_ratio
        }
        
        max_lang = max(ratios.items(), key=lambda x: x[1])
        
        if max_lang[1] > 0.3:  # 30% 이상이면 해당 언어
            return max_lang[0]
        elif korean_ratio + japanese_ratio > 0.3:  # 한일 혼용
            return 'mixed_asian'
        else:
            return 'mixed'
    
    def detect_speaker(self, text, previous_speaker=None, language='unknown'):
        """다국어 화자 감지"""
        text = text.strip()
        
        # 한국어 패턴
        korean_patterns = [
            r'^([가-힣]{2,4})\s*:', # "홍길동:"
            r'^\(([가-힣]{2,4})\)', # "(홍길동)"
            r'^【([가-힣]{2,4})】', # "【홍길동】"
            r'^\[([가-힣]{2,4})\]', # "[홍길동]"
        ]
        
        # 영어 패턴
        english_patterns = [
            r'^([A-Z][a-z]+ [A-Z][a-z]+)\s*:', # "John Smith:"
            r'^([A-Z][a-z]+)\s*:', # "John:"
            r'^\(([A-Z][a-z]+(?: [A-Z][a-z]+)?)\)', # "(John Smith)"
            r'^\[([A-Z][a-z]+(?: [A-Z][a-z]+)?)\]', # "[John Smith]"
            r'^([A-Z]{2,})\s*:', # "HOST:", "CEO:"
        ]
        
        patterns = korean_patterns + english_patterns
        
        for pattern in patterns:
            match = re.match(pattern, text)
            if match:
                return match.group(1), re.sub(pattern, '', text).strip()
        
        # 기본값
        default_speaker = "화자" if language == 'korean' else "Speaker"
        return previous_speaker or default_speaker, text
    
    def collect_subtitles(self, video_url, progress_callback=None):
        """자막 수집"""
        try:
            video_id = self.extract_video_id(video_url)
            
            if progress_callback:
                progress_callback(f"🎬 비디오 처리 중: {video_id}")
            
            # 이미 수집된 영상인지 확인
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            existing = cur.execute(
                "SELECT COUNT(*) FROM captions WHERE video_id = ?", 
                (video_id,)
            ).fetchone()[0]
            
            if existing > 0:
                conn.close()
                return f"⚠️ 이미 수집된 영상: {existing}개 자막 존재"
            
            # 자막 가져오기
            try:
                transcript = YouTubeTranscriptApi.get_transcript(
                    video_id, 
                    languages=['ko', 'ko-KR', 'en', 'en-US', 'en-GB', 'auto']
                )
            except Exception as e:
                conn.close()
                return f"❌ 자막 없음: {str(e)}"
            
            # 비디오 정보 가져오기
            video_info = self.get_video_info_api(video_id)
            
            if progress_callback:
                progress_callback(f"📺 {video_info['title']} - 자막 처리 중...")
            
            # 자막 처리 및 저장
            current_speaker = video_info['channel_name']
            saved_count = 0
            
            for item in transcript:
                text = item['text'].strip()
                if not text or len(text) < 2:
                    continue
                
                start_time = int(item['start'])
                duration = item['duration']
                end_time = int(start_time + duration)
                
                # 언어 감지
                language = self.detect_language(text)
                
                # 화자 감지
                detected_speaker, clean_text = self.detect_speaker(text, current_speaker, language)
                current_speaker = detected_speaker
                
                # DB 저장
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
                except sqlite3.Error:
                    pass
            
            conn.commit()
            conn.close()
            
            return f"✅ 수집 완료: {saved_count}개 자막 저장"
            
        except Exception as e:
            return f"❌ 오류: {str(e)}"
    
    def search_captions(self, query, limit=50, language_filter=None):
        """자막 검색"""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        keywords = query.split()
        
        # 언어 필터
        language_condition = ""
        if language_filter and language_filter != "all":
            language_condition = f" AND language = '{language_filter}'"
        
        if len(keywords) >= 2:
            # 첫 단어는 화자, 나머지는 텍스트
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
            # 단일 키워드
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
    
    def get_smart_search_limit(self, search_mode, api_usage_percent=0, query=""):
        """검색 모드와 상황에 따른 스마트 결과 개수 결정"""
        
        # 쿼리 구체성 분석
        keywords_count = len(query.split()) if query else 1
        
        if search_mode == "free_only":
            # 무료 모드: API 없으므로 넉넉하게
            return 30
        
        elif search_mode == "api_priority":
            # API 우선: 사용량에 따라 조절
            if api_usage_percent < 30:
                return 25  # API 여유 있을 때
            elif api_usage_percent < 70:
                return 20  # 보통일 때
            else:
                return 15  # 부족할 때 절약
        
        else:  # smart 모드
            # 스마트: 쿼리 구체성 + 시간대 고려
            base_limit = 20 if keywords_count == 1 else 15  # 모호하면 더 많이
            
            # 시간대 조정
            current_hour = datetime.now().hour
            if 0 <= current_hour < 6:  # 새벽 (API 리셋 직후)
                return min(base_limit + 10, 30)
            elif 18 <= current_hour < 24:  # 저녁 (절약 모드)
                return max(base_limit - 5, 10)
            else:
                return base_limit
    def search_captions_with_mode(self, query, language_filter=None, search_mode="smart"):
        """검색 모드에 따른 자막 검색 (스마트 개수 제한 적용)"""
        
        # API 사용량 확인
        api_usage_percent = 0
        if hasattr(self, 'get_usage_report'):
            try:
                usage_report = self.get_usage_report()
                if usage_report:
                    api_usage_percent = usage_report.get('usage_percent', 0)
            except:
                pass
        
        # 스마트 제한 계산
        smart_limit = self.get_smart_search_limit(search_mode, api_usage_percent, query)
        
        # 기존 검색 결과 (스마트 제한 적용)
        base_results = self.search_captions(query, smart_limit, language_filter)
        
        if search_mode == "free_only":
            # 무료 모드: API 정보 없이 기본 정보만
            return base_results
        
        elif search_mode == "api_priority":
            # API 우선: 모든 결과에 API 정보 추가 시도
            enhanced_results = []
            api_usage_count = 0
            
            for result in base_results:
                video_id = result[0]
                
                # API로 상세 정보 가져오기 시도
                try:
                    if self.api_key and self.can_make_request(1):
                        api_info, source = self.get_video_info_with_fallback(video_id)
                        
                        if source == "api":
                            api_usage_count += 1
                            # API 정보로 업데이트된 결과
                            enhanced_result = list(result)
                            enhanced_result[1] = api_info.get('title', result[1])  # 더 정확한 제목
                            enhanced_result[2] = api_info.get('channel_name', result[2])  # 더 정확한 채널명
                            enhanced_results.append(tuple(enhanced_result))
                        else:
                            enhanced_results.append(result)
                    else:
                        enhanced_results.append(result)
                        
                except Exception:
                    enhanced_results.append(result)
                
                # API 사용량 제한
                if api_usage_count >= 10:  # 한 번에 최대 10개까지만 API 사용
                    enhanced_results.extend(base_results[len(enhanced_results):])
                    break
            
            return enhanced_results
        
        # 검색 결과와 함께 정보 반환
        return {
            "results": enhanced_results,
            "limit_used": smart_limit,
            "limit_reason": self._get_limit_reason(search_mode, smart_limit, api_usage_percent, query)
        }
    
    def _get_limit_reason(self, search_mode, limit, api_usage_percent, query):
        """제한 이유 설명"""
        keywords_count = len(query.split()) if query else 1
        current_hour = datetime.now().hour
        
        if search_mode == "free_only":
            return f"🆓 무료 모드: {limit}개 결과 (API 할당량 절약)"
        
        elif search_mode == "api_priority":
            if api_usage_percent < 30:
                return f"⚡ API 여유: {limit}개 고품질 결과"
            elif api_usage_percent < 70:
                return f"⚡ API 적정: {limit}개로 효율 유지"
            else:
                return f"⚡ API 절약: {limit}개로 할당량 보존"
        
        else:  # smart
            base_reason = f"🧠 스마트: {limit}개 최적화된 결과"
            
            if 0 <= current_hour < 6:
                return f"{base_reason} (새벽 보너스 적용)"
            elif 18 <= current_hour < 24:
                return f"{base_reason} (저녁 절약 모드)"
            elif keywords_count == 1:
                return f"{base_reason} (일반 키워드로 더 많이)"
            else:
                return f"{base_reason} (구체적 키워드로 적정히)"
    
    def get_stats(self):
        """통계 정보"""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        total_captions = cur.execute("SELECT COUNT(*) FROM captions").fetchone()[0]
        total_videos = cur.execute("SELECT COUNT(DISTINCT video_id) FROM captions").fetchone()[0]
        total_speakers = cur.execute("SELECT COUNT(DISTINCT speaker) FROM captions").fetchone()[0]
        
        # 언어별 통계
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
        
        else:  # smart 모드 (기본)
            # 스마트 모드: 중요한 영상만 선별적으로 API 사용
            enhanced_results = []
            api_usage_count = 0
            
            for i, result in enumerate(base_results):
                video_id = result[0]
                
                # 상위 5개 결과만 API로 상세 정보 가져오기
                if i < 5 and self.api_key and self.can_make_request(1):
                    try:
                        api_info, source = self.get_video_info_with_fallback(video_id)
                        
                        if source == "api":
                            api_usage_count += 1
                            enhanced_result = list(result)
                            enhanced_result[1] = api_info.get('title', result[1])
                            enhanced_result[2] = api_info.get('channel_name', result[2])
                            enhanced_results.append(tuple(enhanced_result))
                        else:
                            enhanced_results.append(result)
                    except:
                        enhanced_results.append(result)
                else:
                    enhanced_results.append(result)
            
            return enhanced_results

# 세션 상태 초기화
if 'finder' not in st.session_state:
    st.session_state.finder = None

# 메인 헤더
st.markdown("""
<div class="main-header">
    <h1>🎬 Korean Clip Finder</h1>
    <p>한국어/영어 학습 & 영상 제작을 위한 유튜브 클립 검색기</p>
    <p><small>각자의 YouTube API 키로 개인 데이터 관리</small></p>
</div>
""", unsafe_allow_html=True)

# 사이드바 - API 설정
st.sidebar.title("⚙️ 설정")

# API 키 입력
st.sidebar.subheader("🔑 YouTube Data API 키")

# 현재 시간 기준 추천 표시
current_hour = datetime.now().hour
if 6 <= current_hour < 12:
    st.sidebar.success("🌅 현재 아침! API 우선 모드 추천")
elif 12 <= current_hour < 18:
    st.sidebar.info("🌞 현재 낮! 스마트 모드 추천")
else:
    st.sidebar.warning("🌙 현재 밤! 무료 전용 추천")

api_key = st.sidebar.text_input(
    "API 키 입력 (선택사항)", 
    type="password",
    help="API 키가 있으면 더 정확한 비디오 정보를 가져올 수 있습니다",
    placeholder="AIza..."
)

# API 키 상태 표시
if api_key:
    st.sidebar.success("✅ API 키가 입력되었습니다!")
    st.sidebar.info("이제 더 정확한 비디오 정보를 가져올 수 있어요")
else:
    st.sidebar.info("💡 API 키 없이도 모든 기능이 정상 작동합니다")

if st.sidebar.button("🔄 API 키 적용"):
    if api_key:
        st.session_state.finder = YouTubeClipFinder(api_key)
        st.sidebar.success("✅ API 키가 성공적으로 적용되었습니다!")
    else:
        st.session_state.finder = YouTubeClipFinder()
        st.sidebar.info("🆓 무료 모드로 설정되었습니다")

# API 키 없이도 사용 가능하지만, 입력된 키가 있으면 사용
if not st.session_state.finder:
    st.session_state.finder = YouTubeClipFinder(api_key if api_key else None)

# API 키 가이드
with st.sidebar.expander("📖 API 키 얻는 방법"):
    st.markdown("""
    1. [Google Cloud Console](https://console.cloud.google.com) 접속
    2. 새 프로젝트 생성
    3. YouTube Data API v3 활성화
    4. 사용자 인증 정보 > API 키 생성
    5. 위에 붙여넣기
    
    **무료 할당량**: 일 10,000 요청
    **API 키 없어도 기본 기능 사용 가능!**
    """)

# 자막 수집 섹션
st.sidebar.subheader("📥 자막 수집")
video_urls = st.sidebar.text_area(
    "YouTube URL (한 줄당 하나)",
    placeholder="https://www.youtube.com/watch?v=...\nhttps://youtu.be/...",
    height=100
)

if st.sidebar.button("🚀 자막 수집 시작"):
    if video_urls.strip():
        urls = [url.strip() for url in video_urls.strip().split('\n') if url.strip()]
        
        progress_bar = st.sidebar.progress(0)
        status_text = st.sidebar.empty()
        
        results = []
        for i, url in enumerate(urls):
            def progress_callback(message):
                status_text.text(message)
            
            result = st.session_state.finder.collect_subtitles(url, progress_callback)
            results.append(f"{i+1}. {result}")
            progress_bar.progress((i + 1) / len(urls))
        
        status_text.text("✅ 수집 완료!")
        for result in results:
            st.sidebar.write(result)

# 통계 표시
stats = st.session_state.finder.get_stats()
st.sidebar.subheader("📊 내 데이터 통계")
col1, col2 = st.sidebar.columns(2)
col1.metric("자막", f"{stats['total_captions']:,}")
col2.metric("영상", f"{stats['total_videos']:,}")
col1.metric("화자", f"{stats['total_speakers']:,}")

if stats['languages']:
    st.sidebar.write("**언어별 분포:**")
    for lang, count in stats['languages'].items():
        st.sidebar.write(f"- {lang}: {count:,}개")

# 사이드바 팁 요약
st.sidebar.markdown("---")
st.sidebar.subheader("💡 **핵심 팁**")
st.sidebar.info("""
**🚀 대용량 수집:**
• API 키 여러 개 사용
• 무료 방식 70% + API 30%
• 자정 이후 배치 수집

**🔍 검색 모드 활용:**
• 🧠 스마트: 균형잡힌 검색 (추천)
• 🆓 무료만: API 할당량 절약
• ⚡ API 우선: 최고 품질 결과

**⚡ 효율적 검색:**
• "화자명 + 키워드" 형식
• 띄어쓰기 정확히
• 인기 채널 우선

**📱 모바일 활용:**
• 폰에서 URL 복사
• 밤에 자동 수집
• 아침에 결과 확인

**🎪 실제 활용 예시:**
• 아침 통근: API 우선 고품질 수집
• 점심시간: 스마트 모드 가벼운 검색
• 저녁 여가: 무료 전용 절약 모드
""")

# 빠른 샘플 URL 버튼들
st.sidebar.markdown("---")
st.sidebar.subheader("🎬 **샘플 URL**")

sample_urls = {
    "🇰🇷 런닝맨": "https://www.youtube.com/watch?v=sample1",
    "🇰🇷 유퀴즈": "https://www.youtube.com/watch?v=sample2", 
    "🇺🇸 TED Talk": "https://www.youtube.com/watch?v=sample3",
    "🇺🇸 Tonight Show": "https://www.youtube.com/watch?v=sample4",
    "🇯🇵 アニメ": "https://www.youtube.com/watch?v=sample5",
    "🇯🇵 バラエティ": "https://www.youtube.com/watch?v=sample6"
}

for name, url in sample_urls.items():
    if st.sidebar.button(name, key=f"sample_{name}"):
        st.session_state['sample_url'] = url
        st.sidebar.success(f"✅ {name} URL 복사됨!")

# 샘플 URL이 선택되면 입력창에 자동 입력
if 'sample_url' in st.session_state:
    st.sidebar.text_area("선택된 URL:", st.session_state['sample_url'], height=50)

# 메인 검색 인터페이스
st.markdown("### 🔍 **클립 검색**")

# 검색 옵션들을 컬럼으로 배치
col1, col2, col3 = st.columns([3, 1, 1])

with col1:
    search_query = st.text_input(
        "",
        placeholder="예: 유재석 정말, Obama think, Gordon Ramsay delicious",
        key="search_input"
    )

with col2:
    language_filter = st.selectbox(
        "언어 필터",
        ["all", "korean", "english", "japanese", "mixed_asian", "mixed"],
        format_func=lambda x: {
            "all": "전체", 
            "korean": "한국어 🇰🇷", 
            "english": "영어 🇺🇸", 
            "japanese": "일본어 🇯🇵",
            "mixed_asian": "한일혼용 🇰🇷🇯🇵",
            "mixed": "혼합"
        }[x]
    )

with col3:
    search_mode = st.selectbox(
        "검색 모드",
        ["smart", "free_only", "api_priority"],
        format_func=lambda x: {
            "smart": "🧠 스마트 (추천)",
            "free_only": "🆓 무료만", 
            "api_priority": "⚡ API 우선"
        }[x],
        help="스마트: 무료→API 순서 / 무료만: API 사용 안함 / API 우선: API→무료 순서"
    )

# 검색 모드별 상세 설명
mode_descriptions = {
    "smart": "🧠 **스마트 모드**: 무료 방식 우선 → API 사용 (70:30 비율, 추천)",
    "free_only": "🆓 **무료 전용**: oEmbed + 스크래핑만 사용 (API 할당량 절약)",
    "api_priority": "⚡ **API 우선**: API 먼저 시도 → 무료 방식 (더 정확하지만 할당량 소모)"
}

# 현재 시간에 따른 추천 모드 표시
from datetime import datetime
current_hour = datetime.now().hour

if 6 <= current_hour < 12:
    time_recommendation = "🌅 **아침 시간대**: API가 가득 찬 상태! API 우선 모드 추천"
    recommended_mode = "api_priority"
elif 12 <= current_hour < 18:
    time_recommendation = "🌞 **낮 시간대**: 일상적 검색에는 스마트 모드가 최적"
    recommended_mode = "smart"
else:
    time_recommendation = "🌙 **저녁/밤 시간대**: API 할당량 절약을 위해 무료 전용 추천"
    recommended_mode = "free_only"

# 선택된 모드와 추천 모드 비교
if search_mode == recommended_mode:
    st.success(f"{mode_descriptions[search_mode]} ✅ **현재 시간대 최적 선택!**")
else:
    st.info(f"{mode_descriptions[search_mode]}")
    st.warning(f"💡 {time_recommendation}")

# 시간대별 전략 가이드
with st.expander("⏰ **시간대별 검색 전략 가이드**", expanded=False):
    st.markdown("""
    ## 📅 **하루 24시간 최적화 전략**
    
    ### 🌅 **아침 (06:00~12:00)** - API 우선 모드 🔥
    ```
    ✅ API 할당량이 가득 찬 상태 (10,000 유닛)
    ✅ 가장 정확하고 상세한 정보 수집 가능
    ✅ 중요한 프로젝트나 연구용 검색 최적
    
    📋 추천 활용:
    • 새 프로젝트 시작할 때
    • 정확한 채널 정보가 필요할 때  
    • 대량의 고품질 데이터 수집
    ```
    
    ### 🌞 **낮 (12:00~18:00)** - 스마트 모드 ⚖️
    ```
    ⚡ 효율성과 품질의 황금 균형
    ⚡ 무료 70% + API 30% 비율로 최적화
    ⚡ 일상적인 검색과 학습에 완벽
    
    📋 추천 활용:
    • 언어 학습용 클립 찾기
    • 일반적인 영상 소스 검색
    • 꾸준한 DB 구축
    ```
    
    ### 🌙 **저녁/밤 (18:00~06:00)** - 무료 전용 💰
    ```
    🛡️ API 할당량 완전 보존
    🛡️ 내일을 위한 전략적 절약
    🛡️ 기본 기능으로도 충분한 검색
    
    📋 추천 활용:
    • 간단한 클립 찾기
    • 대량 URL 배치 수집 준비
    • API 리셋 전 마지막 절약
    ```
    
    ---
    
    ## 🎯 **상황별 모드 선택 가이드**
    
    | 상황 | 추천 모드 | 이유 |
    |------|----------|------|
    | 🎓 **연구/프로젝트 시작** | ⚡ API 우선 | 정확한 메타데이터 필수 |
    | 📚 **일상 언어학습** | 🧠 스마트 | 효율성과 품질 균형 |
    | 🎬 **영상 소스 대량 수집** | 🆓 무료 전용 | 할당량 절약이 중요 |
    | 🔍 **빠른 확인용 검색** | 🆓 무료 전용 | 기본 정보면 충분 |
    | 💼 **중요한 프레젠테이션용** | ⚡ API 우선 | 최고 품질 필요 |
    
    ---
    
    ## 📊 **실시간 API 상태 확인법**
    
    ```python
    # 현재 사용량이 표시되면:
    • 0~30%: ⚡ API 우선 모드 추천
    • 30~70%: 🧠 스마트 모드 추천  
    • 70~100%: 🆓 무료 전용 추천
    ```
    
    **💡 꿀팁**: 자정(00:00)에 API 할당량이 리셋되니까, 자정 직후가 가장 좋은 타이밍이에요!
    
    ---
    
    ## 🎪 **실제 사용 시나리오**
    
    ### 👨‍💼 **직장인 김씨의 하루**
    ```
    07:00 - 출근길에 앱 접속 → "🌅 아침! API 우선 추천" 확인
    09:00 - 프레젠테이션용 영상 검색 → API 우선으로 고품질 수집
    13:00 - 점심시간 가벼운 검색 → "🌞 스마트 모드 추천" 따라하기
    19:00 - 집에서 여가 검색 → "🌙 무료 전용 추천" 보고 절약 모드
    ```
    
    ### 🎓 **언어 학습자 이씨의 활용**
    ```
    아침: API 우선으로 정확한 발음 영상 수집
    낮: 스마트 모드로 일상 표현 찾기  
    밤: 무료 모드로 복습용 클립 검색
    ```
    
    ### 🎬 **유튜버 박씨의 전략**
    ```
    새벽 01:00: API 리셋 직후 대량 소스 수집 (API 우선)
    오전 10:00: 편집용 리액션 클립 찾기 (스마트 모드)
    오후 15:00: 추가 소스 보강 (무료 전용으로 절약)
    저녁 20:00: 최종 확인 및 백업 (무료 전용)
    ```
    
    ### 👨‍🏫 **언어 교사 최씨의 수업 준비**
    ```
    일요일 밤: 다음 주 수업용 영상 대량 수집 (API 우선)
    월~금 아침: 그날 수업 맞춤 클립 검색 (스마트)
    수업 중: 학생 질문에 즉석 검색 (무료 전용)
    ```
    
    ### 🌍 **해외 한국어 학습자 존의 루틴**
    ```
    현지 아침 = 한국 밤: 무료 모드로 기본 표현 연습
    현지 점심 = 한국 새벽: API 우선으로 정확한 발음 학습
    현지 저녁 = 한국 오후: 스마트 모드로 드라마/예능 클립
    ```
    
    ---
    
    ## 🎯 **목적별 최적 활용법**
    
    | 목적 | 최적 시간 | 추천 모드 | 검색 예시 |
    |------|----------|-----------|----------|
    | 📚 **언어 학습** | 아침 | ⚡ API 우선 | "아이유 사랑해" |
    | 🎬 **영상 제작** | 새벽 1-3시 | ⚡ API 우선 | "박명수 웃긴" |
    | 👨‍🏫 **수업 준비** | 일요일 밤 | ⚡ API 우선 | "인사 표현" |
    | 🔍 **일상 검색** | 점심시간 | 🧠 스마트 | "유재석 정말" |
    | 📱 **모바일 사용** | 언제든 | 🆓 무료 전용 | "간단 확인" |
    """)

# 검색 버튼과 예시
col1, col2 = st.columns([1, 3])
with col1:
    search_button = st.button("🔍 검색", use_container_width=True)
with col2:
    st.markdown("**예시:** `유재석 정말` `아이유 사랑` `Trump great` `田中 面白い` `宮崎 映画`")

# 검색 실행
if search_button and search_query:
    with st.spinner("검색 중..."):
        # 검색 모드에 따른 상태 표시
        if search_mode == "api_priority":
            st.info("⚡ API 우선 모드: 더 정확한 정보를 위해 API를 적극 사용합니다")
        elif search_mode == "free_only":  
            st.info("🆓 무료 전용 모드: API를 사용하지 않아 할당량을 절약합니다")
        else:
            st.info("🧠 스마트 모드: 효율적인 API 사용으로 최적의 결과를 제공합니다")
        
        # 모드별 검색 실행 (스마트 제한 적용)
        search_result = st.session_state.finder.search_captions_with_mode(
            search_query, 
            language_filter=language_filter if language_filter != "all" else None,
            search_mode=search_mode
        )
        
        # 결과 추출
        if isinstance(search_result, dict):
            results = search_result["results"]
            limit_used = search_result["limit_used"]
            limit_reason = search_result["limit_reason"]
            
            # 스마트 제한 정보 표시
            st.info(f"📊 {limit_reason}")
        else:
            # 이전 버전 호환성
            results = search_result
            limit_used = len(search_result)
        
        # API 사용량 표시 (API 모드일 때)
        if search_mode != "free_only" and hasattr(st.session_state.finder, 'api_key') and st.session_state.finder.api_key:
            usage_report = st.session_state.finder.get_usage_report()
            if usage_report:
                progress_pct = usage_report['usage_percent']
                st.progress(progress_pct / 100, text=f"오늘 API 사용량: {usage_report['units_used']}/{usage_report['daily_limit']} ({progress_pct:.1f}%)")
        
        if not results:
            st.warning("🔍 검색 결과가 없습니다. 먼저 자막을 수집하거나 다른 키워드를 시도해보세요.")
            
            # 검색 결과 없을 때 추가 팁
            with st.expander("🎯 **검색 결과를 찾기 위한 팁**", expanded=True):
                st.markdown("""
                **🔍 검색어 개선 방법:**
                ```
                ❌ "유재석이 정말 재미있다고 말한 부분"
                ✅ "유재석 정말"
                
                ❌ "Trump's speech about America"  
                ✅ "Trump great"
                ```
                
                **📝 단계적 접근:**
                1. **구체적으로**: "유재석 정말 재미있다"
                2. **단순하게**: "유재석 정말"  
                3. **더 단순히**: "정말"
                4. **화자만**: "유재석"
                
                **🎯 검색 모드 변경:**
                - **무료 전용**: API 없이 빠른 검색
                - **스마트**: 균형잡힌 검색 (추천)
                - **API 우선**: 가장 정확한 검색
                
                **💡 자막 수집 먼저:**
                - 왼쪽 사이드바에서 YouTube URL 입력
                - 인기 영상부터 수집 (자막 확률 높음)
                - 최신 영상 위주로 선택
                """)
        else:
            # 검색 모드별 결과 메시지
            if search_mode == "api_priority":
                st.success(f"⚡ API 우선 검색: {len(results)}개 고품질 결과")
            elif search_mode == "free_only":
                st.success(f"🆓 무료 검색: {len(results)}개 결과 (API 할당량 절약됨)")
            else:
                st.success(f"🧠 스마트 검색: {len(results)}개 최적화된 결과")
            
            # 검색 성공 시 추가 활용 팁
            if len(results) > 10:
                if search_mode == "free_only":
                    st.info(f"💡 **{len(results)}개 결과!** API 우선 모드로 바꾸면 더 정확한 정보를 볼 수 있어요")
                else:
                    st.info(f"💡 **{len(results)}개 결과!** 더 정확한 검색을 원하면 키워드를 추가해보세요 (예: '{search_query} 웃음')")
            
            # 검색어 하이라이트
            def highlight_text(text, query):
                keywords = query.split()
                highlighted = text
                for keyword in keywords:
                    if len(keyword) > 0:
                        highlighted = re.sub(
                            f'({re.escape(keyword)})', 
                            r'**\1**', 
                            highlighted, 
                            flags=re.IGNORECASE
                        )
                return highlighted
            
            # 결과 표시
            for idx, row in enumerate(results):
                video_id, title, channel, speaker, text, start_time, end_time, duration, language = row
                
                with st.container():
                    st.markdown(f"""
                    <div class="result-item">
                        <strong>🎤 {speaker}</strong><br>
                        💬 {highlight_text(text, search_query)}<br>
                        📺 {title} | 📻 {channel}<br>
                        ⏱️ {start_time}초~{end_time}초 ({end_time-start_time}초) | 🌐 {language}<br>
                        <a href="https://www.youtube.com/watch?v={video_id}&t={start_time}s" target="_blank">
                            ▶️ YouTube에서 보기
                        </a>
                    </div>
                    """, unsafe_allow_html=True)

# 안내 메시지
if stats['total_captions'] == 0:
    st.info("""
    👋 **시작하기:**
    1. 왼쪽 사이드바에서 YouTube URL 입력
    2. "자막 수집 시작" 버튼 클릭
    3. 수집 완료 후 위에서 검색!
    
    **API 키가 없어도 사용 가능합니다** (기본 기능 제한 없음)
    """)

# 푸터
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #666;'>
    <p>🎬 <strong>Korean Clip Finder</strong></p>
    <p>각자의 YouTube API로 개인 데이터 관리 | Made with ❤️ for language learners</p>
    <p><small>이 앱은 YouTube의 공개 자막만 사용하며, 저작권을 존중합니다</small></p>
</div>
""", unsafe_allow_html=True)