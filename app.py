import streamlit as st
import pandas as pd
import json
import re
from google import genai
from google.genai import types
from supabase import create_client, Client

# 1. 환경 변수 설정 (Streamlit Secrets)
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# 2. 클라이언트 초기화
gen_client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 앱 레이아웃 설정
st.set_page_config(page_title="AI 뉴스 검색 & 저장기", layout="wide")
st.title("📰 AI 최신 뉴스 검색 & 자동 저장기")

tabs = st.tabs(["🔍 검색하기", "💾 저장된 뉴스 보기", "📊 통계 분석"])

# --- Tab 1: 검색 및 저장 ---
with tabs[0]:
    st.subheader("실시간 뉴스 검색")
    keyword = st.text_input("검색하고 싶은 키워드를 입력하세요 (예: 엔비디아 주가, 한국 AI 정책)")
    search_btn = st.button("뉴스 검색 및 저장 시작")

    if search_btn and keyword:
        with st.spinner("Gemini가 구글 검색을 통해 최신 정보를 분석 중입니다..."):
            try:
                # Gemini API 호출 (Google Search Tool 사용)
                # 주의: JSON 모드와 Search Tool을 동시 사용 불가하므로 프롬프트로 제어
                prompt = f"""
                '{keyword}'에 대한 가장 최신 뉴스 딱 2건만 검색해줘.
                결과는 반드시 아래 JSON 배열 형식으로만 응답해. 
                절대 URL을 지어내지 말고 검색 결과에 있는 실제 URL을 사용해.

                형식:
                [
                  {{"title": "기사제목", "source": "신문사", "news_date": "YYYY-MM-DD", "url": "실제URL", "summary": "3줄 요약"}}
                ]
                """
                
                response = gen_client.models.generate_content(
                    model="gemini-2.0-flash", # 최신 모델 사용
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearchRetrieval())],
                        temperature=0.0
                    ),
                    contents=prompt
                )

                # --- URL 환각 방지 로직 (Grounding Metadata 활용) ---
                raw_text = response.text
                # JSON 부분만 추출
                json_match = re.search(r'\[.*\]', raw_text, re.DOTALL)
                if json_match:
                    news_list = json.loads(json_match.group())
                else:
                    st.error("AI 응답 형식에 오류가 발생했습니다. 다시 시도해 주세요.")
                    st.stop()

                # 실제 참조 링크 추출 및 매칭
                grounding_chunks = response.candidates[0].grounding_metadata.grounding_chunks
                
                final_news = []
                for news in news_list:
                    # 타이틀 유사도 기반으로 실제 URL 교체
                    for chunk in grounding_chunks:
                        if chunk.web:
                            real_title = chunk.web.title
                            real_url = chunk.web.uri
                            # 제목이 포함되어 있고, 가짜 리다이렉트 링크가 아닌 경우에만 교체
                            if (news['title'][:10] in real_title) and ("grounding-api-redirect" not in real_url):
                                news['url'] = real_url
                                break
                    final_news.append(news)

                # 화면 출력 및 DB 저장
                saved_count = 0
                skip_count = 0

                for item in final_news:
                    # 화면 표시
                    with st.container():
                        st.markdown(f"### [{item['title']}]({item['url']})")
                        st.caption(f"출처: {item['source']} | 날짜: {item['news_date']}")
                        st.write(item['summary'])
                        st.divider()
                    
                    # DB 저장
                    try:
                        data = {
                            "keyword": keyword,
                            "title": item['title'],
                            "source": item['source'],
                            "news_date": item['news_date'],
                            "url": item['url'],
                            "summary": item['summary']
                        }
                        supabase.table("news_history").insert(data).execute()
                        saved_count += 1
                    except Exception as e:
                        if "23505" in str(e): # Unique violation
                            skip_count += 1
                        else:
                            st.error(f"저장 오류: {e}")

                st.toast(f"✅ 완료! 새 저장: {saved_count}건 / 중복 생략: {skip_count}건")

            except Exception as e:
                st.error(f"오류가 발생했습니다: {e}")

# --- Tab 2: 저장된 뉴스 보기 ---
with tabs[1]:
    st.subheader("저장된 뉴스 히스토리")
    
    # DB에서 데이터 가져오기
    response = supabase.table("news_history").select("*").order("created_at", desc=True).execute()
    df = pd.DataFrame(response.data)

    if not df.empty:
        # 필터 기능
        search_q = st.text_input("제목 또는 키워드로 결과 내 검색")
        filtered_df = df[df['title'].str.contains(search_q) | df['keyword'].str.contains(search_q)]
        
        st.dataframe(filtered_df, use_container_width=True)
        
        # CSV 다운로드
        csv = filtered_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("CSV로 다운로드", data=csv, file_name="news_history.csv", mime="text/csv")
    else:
        st.info("아직 저장된 뉴스가 없습니다.")

# --- Tab 3: 통계 분석 ---
with tabs[2]:
    st.subheader("데이터 분석 대시보드")
    
    if not df.empty:
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### 🏆 인기 검색 키워드")
            keyword_counts = df['keyword'].value_counts()
            st.bar_chart(keyword_counts)
            
        with col2:
            st.markdown("#### 📅 일자별 저장 건수")
            df['created_date'] = pd.to_datetime(df['created_at']).dt.date
            date_counts = df.groupby('created_date').size()
            st.line_chart(date_counts)
    else:
        st.info("통계를 표시할 데이터가 부족합니다.")