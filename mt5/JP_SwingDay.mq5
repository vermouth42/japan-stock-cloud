//+------------------------------------------------------------------+
//| JP_SwingDay.mq5                                                  |
//| 日本株（東証銘柄のCFDなど）デイトレ・スイング用インジケーター    |
//|                                                                  |
//| 日足以上: 5/25/75MA とスイングシグナル（BO/PB/BD/RS）            |
//| 分足    : VWAP・オープニングレンジ・デイシグナル（ORB/VWAP反発） |
//| ルールは TradingView 版・Python 版と同じ（docs/strategy_spec.md）|
//| ATR・RSI は Wilder 平滑で自前計算（TradingView の ta.* と同じ）  |
//|                                                                  |
//| バッファ 6=買いシグナル, 7=売りシグナル, 17=セットアップ番号     |
//| （EA から iCustom で読める。番号は SetupName() を参照）          |
//+------------------------------------------------------------------+
#property copyright   "japan-stock-cloud"
#property version     "1.00"
#property description "日本株 デイ・スイング用インジケーター（MA・VWAP・OR・シグナル）"
#property indicator_chart_window
#property indicator_buffers 19
#property indicator_plots   8

#property indicator_label1  "MA短"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrOrange
#property indicator_width1  1

#property indicator_label2  "MA中"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrCrimson
#property indicator_width2  2

#property indicator_label3  "MA長"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrRoyalBlue
#property indicator_width3  2

#property indicator_label4  "VWAP"
#property indicator_type4   DRAW_LINE
#property indicator_color4  clrMediumOrchid
#property indicator_width4  2

#property indicator_label5  "OR高値"
#property indicator_type5   DRAW_LINE
#property indicator_color5  clrSeaGreen
#property indicator_style5  STYLE_DOT

#property indicator_label6  "OR安値"
#property indicator_type6   DRAW_LINE
#property indicator_color6  clrTomato
#property indicator_style6  STYLE_DOT

#property indicator_label7  "買いシグナル"
#property indicator_type7   DRAW_ARROW
#property indicator_color7  clrDodgerBlue
#property indicator_width7  2

#property indicator_label8  "売りシグナル"
#property indicator_type8   DRAW_ARROW
#property indicator_color8  clrOrangeRed
#property indicator_width8  2

enum ENUM_JP_MODE
  {
   JP_AUTO  = 0, // 自動（日足未満ならデイ）
   JP_SWING = 1, // スイング
   JP_DAY   = 2  // デイ
  };

input group "モード"
input ENUM_JP_MODE InpMode      = JP_AUTO; // モード
input bool         InpShowShort = true;    // 売りシグナル（信用売り）も表示
input bool         InpRealVol   = true;    // 実出来高を使う（無ければティック出来高）

input group "移動平均・トレンド"
input int    InpMaShort  = 5;  // 短期MA
input int    InpMaMid    = 25; // 中期MA
input int    InpMaLong   = 75; // 長期MA
input int    InpSlopeLen = 5;  // 中期MAの傾き判定（n本前と比較）
input int    InpAtrLen   = 14; // ATR期間
input int    InpRsiLen   = 14; // RSI期間
input int    InpVolLen   = 25; // 出来高倍率の分母（平均出来高の期間）

input group "スイング（日足）シグナル"
input int    InpBoLen       = 20;  // ブレイクアウト: 高値更新の期間
input double InpVolRatioMin = 1.5; // ブレイクアウト: 出来高倍率 以上
input double InpClosePosMin = 0.6; // ブレイクアウト: 引け位置（0〜1）以上
input double InpRsiBoMax    = 80;  // ブレイクアウト: RSI 以下
input double InpDevMax      = 15;  // ブレイクアウト: 中期MA乖離率% 以下
input int    InpPbLook      = 3;   // 押し目: 何本以内に中期MA付近まで押したか
input double InpPbAtr       = 0.5; // 押し目: 中期MA + n×ATR 以内
input double InpRsiPbMin    = 40;  // 押し目: RSI 下限
input double InpRsiPbMax    = 65;  // 押し目: RSI 上限
input double InpRsiRsMin    = 35;  // 戻り売り: RSI 下限
input double InpRsiRsMax    = 60;  // 戻り売り: RSI 上限
input double InpRsiBdMin    = 20;  // ブレイクダウン: RSI 以上
input double InpStopAtr     = 2.0; // 損切り目安 = 終値 ∓ n×ATR
input double InpTpR         = 2.0; // 利確目安（R倍）

input group "デイトレ（分足）"
input bool   InpAutoJst     = true; // サーバー時刻→日本時間の差を自動計算
input int    InpJstMinusSrv = 7;    // 手動: 日本時間 − サーバー時刻（時間）
input int    InpOrMinutes   = 15;   // オープニングレンジ（分）
input int    InpAmStart     = 905;  // 前場の新規エントリー開始（HHMM, JST）
input int    InpAmEnd       = 1115; // 前場の新規エントリー終了
input int    InpPmStart     = 1230; // 後場の新規エントリー開始
input int    InpPmEnd       = 1430; // 後場の新規エントリー終了
input int    InpDayVolLen   = 20;   // 出来高平均（本数）
input double InpDayVolMult  = 1.5;  // ORB: 出来高が平均の n 倍以上
input double InpMaxGap      = 5.0;  // ギャップ上限%（超えたら順張りしない）
input double InpVwTol       = 0.3;  // VWAP反発: VWAP + n×ATR まで押した
input bool   InpUseDTrend   = true; // 日足トレンドに沿う方向だけ（前日終値 vs 日足中期MA）
input double InpDayStopAtr  = 1.5;  // 損切り目安 = 終値 ∓ n×ATR（分足ATR）
input double InpDayTpR      = 1.5;  // 利確目安（R倍）

input group "アラート・表示"
input bool   InpAlert = true;  // 足確定でシグナルが出たらアラート
input bool   InpPush  = false; // スマホにプッシュ通知（MT5 の通知設定が必要）
input bool   InpPanel = true;  // 左上に情報パネルを表示

//--- 描画バッファ
double BufMaS[], BufMaM[], BufMaL[], BufVwap[], BufOrH[], BufOrL[], BufBuy[], BufSell[];
//--- 計算用バッファ
double BufAtr[], BufAvgUp[], BufAvgDn[], BufRsi[], BufCumPV[], BufCumV[], BufDayStart[];
double BufRunH[], BufRunL[], BufSetup[], BufDayOpen[];

bool     g_isDay = false;
int      g_offsetSec = 0;     // 日本時間 − サーバー時刻（秒）
double   g_dayCloses[];       // 日本時間の日ごとの終値（前日まで）
long     g_lastDayKey = -1;
datetime g_lastAlertBar = 0;
string   g_lastSig = "";      // 直近（確定足）のシグナル説明

//+------------------------------------------------------------------+
bool Ok(const double v) { return v != EMPTY_VALUE; }

long DayKey(const datetime t) { return (long)((long)t + g_offsetSec) / 86400; }

int HhmmJst(const datetime t)
  {
   MqlDateTime dt;
   TimeToStruct((datetime)((long)t + g_offsetSec), dt);
   return dt.hour * 100 + dt.min;
  }

double SmaArr(const double &a[], const int i, const int len)
  {
   if(len <= 0 || i < len - 1)
      return EMPTY_VALUE;
   double s = 0.0;
   for(int k = i - len + 1; k <= i; k++)
      s += a[k];
   return s / len;
  }

double HighestArr(const double &a[], const int last, const int len)
  {
   if(len <= 0 || last < len - 1)
      return EMPTY_VALUE;
   double m = a[last];
   for(int k = last - len + 1; k < last; k++)
      if(a[k] > m)
         m = a[k];
   return m;
  }

double LowestArr(const double &a[], const int last, const int len)
  {
   if(len <= 0 || last < len - 1)
      return EMPTY_VALUE;
   double m = a[last];
   for(int k = last - len + 1; k < last; k++)
      if(a[k] < m)
         m = a[k];
   return m;
  }

double VolAt(const long &tickVol[], const long &realVol[], const int i)
  {
   if(InpRealVol && realVol[i] > 0)
      return (double)realVol[i];
   return (double)tickVol[i];
  }

double SmaVol(const long &tickVol[], const long &realVol[], const int i, const int len)
  {
   if(len <= 0 || i < len - 1)
      return EMPTY_VALUE;
   double s = 0.0;
   for(int k = i - len + 1; k <= i; k++)
      s += VolAt(tickVol, realVol, k);
   return s / len;
  }

double TrAt(const double &h[], const double &l[], const double &c[], const int i)
  {
   double hl = h[i] - l[i];
   if(i == 0)
      return hl;
   return MathMax(hl, MathMax(MathAbs(h[i] - c[i - 1]), MathAbs(l[i] - c[i - 1])));
  }

double VwapAt(const double &h[], const double &l[], const double &c[], const int k)
  {
   if(BufCumV[k] > 0)
      return BufCumPV[k] / BufCumV[k];
   return (h[k] + l[k] + c[k]) / 3.0;
  }

double DailyMa()
  {
   int n = ArraySize(g_dayCloses);
   if(InpMaMid <= 0 || n < InpMaMid)
      return EMPTY_VALUE;
   double s = 0.0;
   for(int k = n - InpMaMid; k < n; k++)
      s += g_dayCloses[k];
   return s / InpMaMid;
  }

double PrevDayClose()
  {
   int n = ArraySize(g_dayCloses);
   return n > 0 ? g_dayCloses[n - 1] : EMPTY_VALUE;
  }

bool IsBuySetup(const int s) { return s == 1 || s == 2 || s == 5 || s == 6; }

string SetupName(const int s)
  {
   switch(s)
     {
      case 1: return "BO ブレイクアウト買い";
      case 2: return "PB 押し目買い";
      case 3: return "BD ブレイクダウン売り";
      case 4: return "RS 戻り売り";
      case 5: return "ORB 買い";
      case 6: return "VWAP 反発買い";
      case 7: return "ORB 売り";
      case 8: return "VWAP 反落売り";
     }
   return "";
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   SetIndexBuffer(0, BufMaS, INDICATOR_DATA);
   SetIndexBuffer(1, BufMaM, INDICATOR_DATA);
   SetIndexBuffer(2, BufMaL, INDICATOR_DATA);
   SetIndexBuffer(3, BufVwap, INDICATOR_DATA);
   SetIndexBuffer(4, BufOrH, INDICATOR_DATA);
   SetIndexBuffer(5, BufOrL, INDICATOR_DATA);
   SetIndexBuffer(6, BufBuy, INDICATOR_DATA);
   SetIndexBuffer(7, BufSell, INDICATOR_DATA);
   SetIndexBuffer(8, BufAtr, INDICATOR_CALCULATIONS);
   SetIndexBuffer(9, BufAvgUp, INDICATOR_CALCULATIONS);
   SetIndexBuffer(10, BufAvgDn, INDICATOR_CALCULATIONS);
   SetIndexBuffer(11, BufRsi, INDICATOR_CALCULATIONS);
   SetIndexBuffer(12, BufCumPV, INDICATOR_CALCULATIONS);
   SetIndexBuffer(13, BufCumV, INDICATOR_CALCULATIONS);
   SetIndexBuffer(14, BufDayStart, INDICATOR_CALCULATIONS);
   SetIndexBuffer(15, BufRunH, INDICATOR_CALCULATIONS);
   SetIndexBuffer(16, BufRunL, INDICATOR_CALCULATIONS);
   SetIndexBuffer(17, BufSetup, INDICATOR_CALCULATIONS);
   SetIndexBuffer(18, BufDayOpen, INDICATOR_CALCULATIONS);

   for(int p = 0; p < 8; p++)
      PlotIndexSetDouble(p, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetInteger(6, PLOT_ARROW, 233);
   PlotIndexSetInteger(7, PLOT_ARROW, 234);
   PlotIndexSetInteger(6, PLOT_ARROW_SHIFT, 12);
   PlotIndexSetInteger(7, PLOT_ARROW_SHIFT, -12);

   g_isDay = (InpMode == JP_DAY) || (InpMode == JP_AUTO && PeriodSeconds(_Period) < 86400);
   if(InpAutoJst)
     {
      long srvOffset = (long)TimeTradeServer() - (long)TimeGMT();
      srvOffset = (long)MathRound(srvOffset / 1800.0) * 1800; // 30分単位に丸める
      g_offsetSec = (int)(9 * 3600 - srvOffset);
     }
   else
      g_offsetSec = InpJstMinusSrv * 3600;

   IndicatorSetString(INDICATOR_SHORTNAME, g_isDay ? "JP-SD デイ" : "JP-SD スイング");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   Comment("");
  }

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
  {
   if(rates_total < 3)
      return 0;
   if(prev_calculated <= 0)
     {
      ArrayResize(g_dayCloses, 0);
      g_lastDayKey = -1;
      g_lastSig = "";
     }
   int start = prev_calculated > 0 ? prev_calculated - 1 : 0;

   for(int i = start; i < rates_total; i++)
     {
      //--- 移動平均
      BufMaS[i] = SmaArr(close, i, InpMaShort);
      BufMaM[i] = SmaArr(close, i, InpMaMid);
      BufMaL[i] = SmaArr(close, i, InpMaLong);

      //--- ATR（Wilder）
      if(i < InpAtrLen - 1)
         BufAtr[i] = EMPTY_VALUE;
      else
         if(i == InpAtrLen - 1)
           {
            double s = 0.0;
            for(int k = 0; k <= i; k++)
               s += TrAt(high, low, close, k);
            BufAtr[i] = s / InpAtrLen;
           }
         else
            BufAtr[i] = (BufAtr[i - 1] * (InpAtrLen - 1) + TrAt(high, low, close, i)) / InpAtrLen;

      //--- RSI（Wilder）
      if(i < InpRsiLen)
        {
         BufAvgUp[i] = EMPTY_VALUE;
         BufAvgDn[i] = EMPTY_VALUE;
         BufRsi[i] = EMPTY_VALUE;
        }
      else
        {
         if(i == InpRsiLen)
           {
            double su = 0.0, sd = 0.0;
            for(int k = 1; k <= i; k++)
              {
               double d = close[k] - close[k - 1];
               su += MathMax(d, 0.0);
               sd += MathMax(-d, 0.0);
              }
            BufAvgUp[i] = su / InpRsiLen;
            BufAvgDn[i] = sd / InpRsiLen;
           }
         else
           {
            double d = close[i] - close[i - 1];
            BufAvgUp[i] = (BufAvgUp[i - 1] * (InpRsiLen - 1) + MathMax(d, 0.0)) / InpRsiLen;
            BufAvgDn[i] = (BufAvgDn[i - 1] * (InpRsiLen - 1) + MathMax(-d, 0.0)) / InpRsiLen;
           }
         if(BufAvgDn[i] == 0.0)
            BufRsi[i] = 100.0;
         else
            if(BufAvgUp[i] == 0.0)
               BufRsi[i] = 0.0;
            else
               BufRsi[i] = 100.0 - 100.0 / (1.0 + BufAvgUp[i] / BufAvgDn[i]);
        }

      //--- 日本時間の日ごと: VWAP・オープニングレンジ・前日終値
      bool newDay = (i == 0) || (DayKey(time[i]) != DayKey(time[i - 1]));
      double v = VolAt(tick_volume, volume, i);
      double tp = (high[i] + low[i] + close[i]) / 3.0;
      if(newDay)
        {
         BufCumPV[i] = tp * v;
         BufCumV[i] = v;
         BufDayStart[i] = (double)time[i];
         BufDayOpen[i] = open[i];
         BufRunH[i] = high[i];
         BufRunL[i] = low[i];
         long key = DayKey(time[i]);
         if(i > 0 && key > g_lastDayKey)
           {
            int n = ArraySize(g_dayCloses);
            ArrayResize(g_dayCloses, n + 1, 1000);
            g_dayCloses[n] = close[i - 1];
           }
         if(key > g_lastDayKey)
            g_lastDayKey = key;
        }
      else
        {
         BufCumPV[i] = BufCumPV[i - 1] + tp * v;
         BufCumV[i] = BufCumV[i - 1] + v;
         BufDayStart[i] = BufDayStart[i - 1];
         BufDayOpen[i] = BufDayOpen[i - 1];
         bool inOr = (double)time[i] < BufDayStart[i] + InpOrMinutes * 60;
         BufRunH[i] = inOr ? MathMax(BufRunH[i - 1], high[i]) : BufRunH[i - 1];
         BufRunL[i] = inOr ? MathMin(BufRunL[i - 1], low[i]) : BufRunL[i - 1];
        }
      bool orReady = (double)time[i] >= BufDayStart[i] + InpOrMinutes * 60;
      double vw = VwapAt(high, low, close, i);
      BufVwap[i] = (g_isDay && !newDay) ? vw : EMPTY_VALUE;
      BufOrH[i] = (g_isDay && orReady) ? BufRunH[i] : EMPTY_VALUE;
      BufOrL[i] = (g_isDay && orReady) ? BufRunL[i] : EMPTY_VALUE;

      //--- シグナル
      BufBuy[i] = EMPTY_VALUE;
      BufSell[i] = EMPTY_VALUE;
      BufSetup[i] = 0;
      double atr = BufAtr[i];
      int setup = 0;

      if(!g_isDay && i >= 1)
        {
         double maS = BufMaS[i], maM = BufMaM[i], maL = BufMaL[i];
         double maMPrev = (i >= InpSlopeLen) ? BufMaM[i - InpSlopeLen] : EMPTY_VALUE;
         double rsi = BufRsi[i];
         if(Ok(maS) && Ok(maM) && Ok(maL) && Ok(maMPrev) && Ok(atr) && Ok(rsi) && maM != 0.0)
           {
            double volMa = SmaVol(tick_volume, volume, i, InpVolLen);
            double volRatio = (Ok(volMa) && volMa > 0) ? v / volMa : EMPTY_VALUE;
            double rng = high[i] - low[i];
            double closePos = rng > 0 ? (close[i] - low[i]) / rng : 0.5;
            double dev = (close[i] - maM) / maM * 100.0;
            double hhPrev = HighestArr(high, i - 1, InpBoLen);
            double llPrev = LowestArr(low, i - 1, InpBoLen);
            double lowPb = LowestArr(low, i, InpPbLook);
            double highPb = HighestArr(high, i, InpPbLook);
            bool up = close[i] > maL && maM > maL && maM > maMPrev;
            bool down = close[i] < maL && maM < maL && maM < maMPrev;

            bool bo = Ok(hhPrev) && Ok(volRatio) && close[i] > hhPrev && volRatio >= InpVolRatioMin &&
                      closePos >= InpClosePosMin && close[i] > maM && maM >= maMPrev &&
                      rsi <= InpRsiBoMax && dev <= InpDevMax;
            bool pb = !bo && up && Ok(lowPb) && lowPb <= maM + InpPbAtr * atr && close[i] > maM &&
                      close[i] > high[i - 1] && close[i] > open[i] && rsi >= InpRsiPbMin && rsi <= InpRsiPbMax;
            bool bd = !bo && !pb && Ok(llPrev) && Ok(volRatio) && close[i] < llPrev &&
                      volRatio >= InpVolRatioMin && closePos <= 1.0 - InpClosePosMin && close[i] < maM &&
                      maM <= maMPrev && rsi >= InpRsiBdMin && dev >= -InpDevMax;
            bool rs = !bo && !pb && !bd && down && Ok(highPb) && highPb >= maM - InpPbAtr * atr &&
                      close[i] < maM && close[i] < low[i - 1] && close[i] < open[i] &&
                      rsi >= InpRsiRsMin && rsi <= InpRsiRsMax;

            if(bo)
               setup = 1;
            else
               if(pb)
                  setup = 2;
               else
                  if(bd && InpShowShort)
                     setup = 3;
                  else
                     if(rs && InpShowShort)
                        setup = 4;
           }
        }

      if(g_isDay && i >= 3 && orReady && Ok(atr))
        {
         int hm = HhmmJst(time[i]);
         bool inWin = (hm >= InpAmStart && hm < InpAmEnd) || (hm >= InpPmStart && hm < InpPmEnd);
         double pdC = PrevDayClose();
         double dMa = DailyMa();
         bool dUp = !InpUseDTrend || !Ok(dMa) || (Ok(pdC) && pdC > dMa);
         bool dDn = !InpUseDTrend || !Ok(dMa) || (Ok(pdC) && pdC < dMa);
         double dVolMa = SmaVol(tick_volume, volume, i, InpDayVolLen);
         bool volOk = Ok(dVolMa) && dVolMa > 0 && v >= dVolMa * InpDayVolMult;
         double gap = (Ok(pdC) && pdC > 0) ? (BufDayOpen[i] - pdC) / pdC * 100.0 : EMPTY_VALUE;
         bool gapOkL = !Ok(gap) || gap <= InpMaxGap;
         bool gapOkS = !Ok(gap) || gap >= -InpMaxGap;
         double orH = BufRunH[i], orL = BufRunL[i], orMid = (orH + orL) / 2.0;
         double vw3 = VwapAt(high, low, close, i - 3);

         bool orbL = inWin && close[i] > orH && close[i - 1] <= orH && close[i] > vw && volOk && dUp && gapOkL;
         bool orbS = inWin && close[i] < orL && close[i - 1] >= orL && close[i] < vw && volOk && dDn && gapOkS;
         bool vwbL = inWin && !orbL && close[i] > vw && low[i] <= vw + InpVwTol * atr && close[i] > open[i] &&
                     vw > vw3 && close[i] > orMid && dUp;
         bool vwbS = inWin && !orbS && close[i] < vw && high[i] >= vw - InpVwTol * atr && close[i] < open[i] &&
                     vw < vw3 && close[i] < orMid && dDn;

         if(orbL)
            setup = 5;
         else
            if(vwbL)
               setup = 6;
            else
               if(orbS && InpShowShort)
                  setup = 7;
               else
                  if(vwbS && InpShowShort)
                     setup = 8;
        }

      if(setup > 0)
        {
         BufSetup[i] = setup;
         bool isBuy = IsBuySetup(setup);
         if(isBuy)
            BufBuy[i] = low[i];
         else
            BufSell[i] = high[i];
         if(i < rates_total - 1 && Ok(atr))
           {
            double side = isBuy ? 1.0 : -1.0;
            double risk = (g_isDay ? InpDayStopAtr : InpStopAtr) * atr;
            double sl = close[i] - side * risk;
            double tpPrice = close[i] + side * (g_isDay ? InpDayTpR : InpTpR) * risk;
            g_lastSig = StringFormat("%s  %s  終値 %s / 損切り %s / 利確 %s",
                                     TimeToString(time[i], TIME_DATE | TIME_MINUTES), SetupName(setup),
                                     DoubleToString(close[i], _Digits), DoubleToString(sl, _Digits),
                                     DoubleToString(tpPrice, _Digits));
           }
        }
     }

   //--- アラート（確定した直前の足にシグナルが出たとき、1回だけ）
   int k = rates_total - 2;
   if(prev_calculated <= 0)
      g_lastAlertBar = time[k];
   else
      if(InpAlert && time[k] != g_lastAlertBar && BufSetup[k] > 0)
        {
         g_lastAlertBar = time[k];
         string msg = _Symbol + " " + EnumToString(_Period) + " " + g_lastSig;
         Alert(msg);
         if(InpPush)
            SendNotification(msg);
        }

   //--- 情報パネル
   if(InpPanel)
     {
      int i = rates_total - 1;
      double maM = BufMaM[i], maL = BufMaL[i], maS = BufMaS[i];
      double maMPrev = (i >= InpSlopeLen) ? BufMaM[i - InpSlopeLen] : EMPTY_VALUE;
      string trend = "データ不足";
      if(Ok(maS) && Ok(maM) && Ok(maL) && Ok(maMPrev))
        {
         bool up = close[i] > maL && maM > maL && maM > maMPrev;
         bool down = close[i] < maL && maM < maL && maM < maMPrev;
         if(up)
            trend = (maS > maM && maM > maL) ? "上昇（パーフェクトオーダー）" : "上昇";
         else
            if(down)
               trend = (maS < maM && maM < maL) ? "下降（逆パーフェクトオーダー）" : "下降";
            else
               trend = "もみ合い";
        }
      double volMa = SmaVol(tick_volume, volume, i, InpVolLen);
      double volRatio = (Ok(volMa) && volMa > 0) ? VolAt(tick_volume, volume, i) / volMa : EMPTY_VALUE;
      string s = g_isDay ? "JP-SD デイ" : "JP-SD スイング";
      s += "  " + _Symbol + "\n";
      s += "トレンド: " + trend + "\n";
      s += "RSI" + IntegerToString(InpRsiLen) + ": " + (Ok(BufRsi[i]) ? DoubleToString(BufRsi[i], 1) : "—");
      s += "   ATR%: " + (Ok(BufAtr[i]) && close[i] > 0 ? DoubleToString(BufAtr[i] / close[i] * 100.0, 2) + "%" : "—") + "\n";
      s += "出来高倍率: " + (Ok(volRatio) ? DoubleToString(volRatio, 2) : "—");
      s += "   中期MA乖離: " + (Ok(maM) && maM != 0 ? DoubleToString((close[i] - maM) / maM * 100.0, 1) + "%" : "—") + "\n";
      if(g_isDay)
        {
         double vw = VwapAt(high, low, close, i);
         s += "VWAP: " + DoubleToString(vw, _Digits) + "（乖離 " + DoubleToString((close[i] - vw) / vw * 100.0, 2) + "%）\n";
         double pdC = PrevDayClose();
         if(Ok(pdC) && pdC > 0)
            s += "前日終値: " + DoubleToString(pdC, _Digits) + "   寄りギャップ: " +
                 DoubleToString((BufDayOpen[i] - pdC) / pdC * 100.0, 2) + "%\n";
        }
      s += "直近シグナル: " + (g_lastSig == "" ? "なし" : g_lastSig);
      Comment(s);
     }

   return rates_total;
  }
//+------------------------------------------------------------------+
