#import "@preview/metropolyst:0.1.0": *

#let accent-color = rgb(118, 153, 15)
#let mdot = sym.dot.c

#show: metropolyst-theme.with(
  font: "Source Han Sans KR",
  footer-progress: true,
  header-size: 20pt,
  header-weight: "medium",
  main-background-color: luma(100%),
  accent-color: accent-color,
  header-background-color: rgb("#546E7A"),
  config-info(
    title: [
      「제로에너지건축물 온실가스감축 성과분석 및 \
      인증제 고도화 방안 연구」 제안
    ],
    subtitle: [온실가스 감축 성과 분석 방안],
    author: [박관용],
    date: datetime.today(),
  ),
)

#title-slide()

#set text(
  font: "Source Han Sans KR",
  weight: 350,
  size: 16pt,
)
#show math.equation: set text(
  font: ("New Computer Modern Math", "Source Han Sans KR"),
)
#show raw: set text(font: ("Sarasa Term K", "AdwaitaMono Nerd Font"))
#show figure: set text(size: 0.8em)
#set list(
  spacing: 1.2em,
  marker: (
    sym.bullet,
    sym.bullet.stroked,
    sym.square.filled.tiny,
    sym.square.stroked.tiny,
    sym.dot.c,
    sym.bullet.hyph,
  ),
)
#show strong: set text(weight: 400, fill: rgb("#071F60"))
#show link: it => {
  set text(fill: accent-color)
  [#it#h(0.05em)#text(font: "AdwaitaMono Nerd Font", size: 0.6em, " ")]
}
#show emph: it => {
  show regex("[\p{Hangul}\p{Han}\p{Hiragana}\p{Katakana}]"): it => {
    box(skew(it, ax: -12deg, reflow: false), width: 0.9em)
  }
  it
}
#show "->": sym.arrow

== 2025년 Baseline 가정

=== 기준 물량 가정

- \<건축HUB>의 인허가 데이터 기반 용도별 *신축물량* 통계 데이터 취득 \
  - *(신축면적)* 비주거, 주거 모두 *최근 10년 단순 평균* 물량이 이어진다고 가정
- 2018년 이후 공공/민간, 주거/비주거별 신축 면적 중 *인증 비율* 분석
- $"신축면적" times "인증 비율" = "인증 물량"$

#v(0.5em)
=== 기준 원단위
- *2018년 기준 단일 값 적용*
- 용도별 연면적: 국토부 용도별 건축물 현황통계 참조 \
  (주거: 1,772,232,574 m², 비주거: 1,981,895,025 m²)
- 에너지원단위: 에너지경제연구원 \<에너지수급통계> 건물 부문 에너지사용량
  *41.5 MTOE* 적용
- 온실가수 배출 원단위: \<국가 온실가스 인벤토리> *47백만 tCO₂* 적용
- *직간접 배출 구분 불가*

#let shin2025 = text(
  size: 16pt,
)[
  #cite(<Shin_Seo_Jung_Kim_Kim_Kim_Jeong_2025>, form: "prose").
  Carbon emission reduction scenarios in the building sector to achieve \
  carbon neutrality in South Korea by 2050
]

== #shin2025
#slide(composer: (2fr, 1fr))[
  === 기본 물량 추정
  #v(0.5em)

  - 기준연도(2018년)의 물량으로부터 주거·비주거 신축 면적, 멸실률을 반영해 다음해 면적 추정
    - $A_"gross,k" = A_"gross,k-1" - A_"destruction,k" + A_"new,k"$
      #[
        #set text(size: 0.9em)
        - $A_"gross,k"$: $k$년의 전체 연면적
        - $A_"destruction,k"$: $k$년의 멸실 면적
        - $A_"new,k"$: $k$년의 신축 면적
      ]

  - *멸실률*: 국토부 통계로부터 추정
    --- _통계 연도별로 차이가 없어_ 미래 추정량도 같은 수치 적용

  - *신축 면적*
    - 주거: 통계청 미래인구추계 *인구증가율*에 비례 가정
    - 비주거: KDI 한국개발연구원 *장기 경제성장률*(--2050)로 추정
][
  #figure(image("assets/destruction.png", width: 6cm), caption: [멸실률])
  #figure(image("assets/GFA.png", width: 6cm), caption: [연면적 추정 결과])
]

== #shin2025
=== @Shin_Seo_Jung_Kim_Kim_Kim_Jeong_2025 유형별 물량 추정
#v(0.5em)

- 단독주택, 공동주택, 상업, 기타로 유형을 구분해 5년 간격으로 총 면적 추정
- $A_"sector,n,k" =
  A_"sector,n-5,k-1"
  + A_"sector,n,k-1"
  - (A_"destruction,k" times "AR"_"sector" times D_n)$
  - $A_"sector,n,k"$: Sector별 완공 후 $n$년 경과 건물의 총 면적
    ($n$은 5년 단위로 평가)
  - $"AR"_"sector"$: 전체 면적 중 sector의 면적비
  - $D$: 기존 건물의 멸실률
- 에너지 소비량은 한국 부동산원 데이터베이스 이용
  -> 본 연구에선 ECO2 결과로 대체 가능

=== 제안 Baseline 물량 추정 방법
- #cite(<Shin_Seo_Jung_Kim_Kim_Kim_Jeong_2025>, form: "prose")의 방법 중
  $A_"destruction"$과 $D$ 중복 사용 부분이 모호
- 가능하면 처음부터 주거, 비주거, 세부 유형별 $A_"gross,sector"$ 추정

== 사용·저감량 추정 방안: 통계자료

#[
  #set list(spacing: 0.7em)

  === KESIS 국가에너지통계종합정보시스템 자료
  - #link("https://kesis.keei.re.kr/menu.es?mid=a10201010100", [에너지총조사])
    - 건물 부문: (용도, 규모, 건축연도별) *에너지원별* 소비량 및 신재생 생산량 통계
    - 가정 부문: 가구당 *에너지원별* 소비량 통계
    - *3년 주기 자료*, 2022년 마지막
  - #link("https://kesis.keei.re.kr/menu.es?mid=a10202010100", [가구에너지패널조사])
    - 유형(단독, 아파트, 다세대/연립/기타)별, *연료*, 면적, 가구원수,
      연령대별 소비량 통계
    - *연간 자료*, 2022년 마지막
  - #link(
      "https://kesis.keei.re.kr/board.es?mid=a10301020000&bid=0014",
      [에너지통계월보·연보],
    )
    - 국가 전체 *에너지원*, 부문별 통계 (비율 참조)
    - *월간·연간 자료*, 2025년 마지막
  - #link(
      "https://kesis.keei.re.kr/board.es?mid=a10302050000&bid=0022",
      text(weight: "bold")[KEEI 장기 에너지수요전망],
    )
    - 2050년까지 장기 수요 전망 자료, *에너지원별 수요 전망* 포함
      (2024년 마지막)
    - 가정(총, 인당, 가구당, 호당), 산업, 수송, 서비스 부문 구분
  - #link(
      "https://kesis.keei.re.kr/board.es?mid=a10302080000&bid=0063",
      text(weight: "bold")[2025 장기 에너지 시나리오],
    )
    - *STEM 모형 분석, 인구·경제성장률 전망 참조*
]

#pagebreak()

#slide(composer: (1.25fr, 1fr))[
  #set list(spacing: 0.9em)

  === KEEI 장기 에너지수요전망 활용 방안
  - 2000, 2023, 2030, 2040, 2050년 전망 데이터 제공
  - _물량 분석 생략 가능_
  - *주거*: 가정 부문 이용
    - 단독, 아파트, 공동주택 각 용도 #text(size: 0.8em)[(난방, 냉방, 취사, 조명, 기타)]
      및 \ 에너지원별 수요
  - *비주거*: 서비스 부문 이용
    - 업종 구분: 도소매, 숙박음식, 운수보관, 정보통신, 공공행정 및 국방,
      교육서비스, 의료복지, 예술#mdot;스포츠#mdot;레저, 기타서비스
    - 에너지 구분: 석유, 도시가스, 전기, 지역난방, 신재생#mdot;기타
    - 부문: 상업 서비스, 공공 서비스
    - 대표 모델 분류 기준에 따라 KEEI 통계 분류 후 면적 가중 평균 사용
][
  #figure(
    image("assets/KEEI-residential.png"),
    caption: [가정 부문 에너지 상품별 \ 수요와 온실가스 배출 전망],
  )
]

#pagebreak()

// TODO 통계자료 조사
- #link("https://stat.molit.go.kr", [국토교통부 통계누리]) 건축물통계
- #link("https://www.greentogether.go.kr", [그린투게더])
- #link("https://www.hub.go.kr", [건축HUB])
- #link(
    "https://www.energy.or.kr/front/board/List9.do",
    [한국에너지공단 에너지사용량 통계],
  )
- #link("https://www.gir.go.kr/", [온실가스종합정보센터])

== 사용·저감량 추정 방안

- 에너지 통계와 연구에 활용한 대표 모델의 _에너지원별 사용량 비중에 차이_ 존재
  - 국가 통계와 대표 모델의 유형별 EUI 차이, 에너지원의 비중 차이를 사전에 확인 필요
- 건물 유형#mdot;에너지원별 통계에 냉난방급탕조명 사용 비율과
  대표모델의 ZEB 등급별 소요량/탄소 저감률 곱하기
- 대표모델의 탄소배출량, 비중 분산을 평가하고 추정량의 신뢰구간 제시
  - 대표모델 유형별 $sigma_E$, $sigma_("CO"_2)$, $sigma_r$ 명시
  - 저감률 $r$의 추정치 신뢰구간 제시 (t 분포 가정) \
    $macron(r) plus.minus t_(alpha \/ 2) s_r/sqrt(n)$

- TODO 전전화 시나리오의 경우 단순 저감량 곱 계산이 어려움 -> 대체 계산 방법 필요

// =============================================================================
#pagebreak()
#{
  set text(font: "Source Sans 3", size: 16pt, weight: "regular")
  show link: it => it.body
  bibliography("ref.bib", style: "apa")
}
