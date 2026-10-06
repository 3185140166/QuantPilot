<script setup lang="ts">
import { ElFormItem } from "element-plus/es/components/form/index.mjs";
import { ElInput } from "element-plus/es/components/input/index.mjs";
import { ElInputNumber } from "element-plus/es/components/input-number/index.mjs";
import { ElOption } from "element-plus/es/components/select/index.mjs";
import { ElSelect } from "element-plus/es/components/select/index.mjs";
import { ElSwitch } from "element-plus/es/components/switch/index.mjs";
import type { StrategyRules } from "../types";
defineProps<{ accountRiskMode?: boolean; simpleMode?: boolean }>();
const rules = defineModel<StrategyRules>({ required: true });
</script>

<template>
  <div class="form-grid rule-fields">
    <el-form-item label="策略模板">
      <el-select v-model="rules.strategy_type" aria-label="策略模板">
        <el-option label="EMA / RSI 趋势" value="ema_rsi" />
        <el-option label="EMA 双均线" value="ema_cross" />
        <el-option label="RSI 均值回归" value="rsi_reversion" />
        <el-option label="区间突破" value="breakout" />
        <el-option label="布林带回归" value="bollinger_reversion" />
      </el-select>
    </el-form-item>
    <el-form-item label="配置版本"
      ><el-input
        :model-value="`v${rules.version} · 每次回测保存参数快照`"
        disabled
    /></el-form-item>
    <el-form-item label="快 EMA"
      ><el-input-number
        v-model="rules.fast"
        :min="2"
        :max="499"
        :disabled="!['ema_rsi', 'ema_cross'].includes(rules.strategy_type)"
    /></el-form-item>
    <el-form-item label="慢 EMA"
      ><el-input-number
        v-model="rules.slow"
        :min="3"
        :max="rules.hourly_trend_filter ? 200 : 500"
        :disabled="!['ema_rsi', 'ema_cross'].includes(rules.strategy_type)"
    /></el-form-item>
    <el-form-item label="RSI 周期"
      ><el-input-number
        v-model="rules.rsi_period"
        :min="2"
        :max="100"
        :disabled="!['ema_rsi', 'rsi_reversion'].includes(rules.strategy_type)"
    /></el-form-item>
    <el-form-item label="RSI 下限"
      ><el-input-number
        v-model="rules.rsi_lower"
        :min="1"
        :max="99"
        :disabled="!['ema_rsi', 'rsi_reversion'].includes(rules.strategy_type)"
    /></el-form-item>
    <el-form-item label="RSI 上限"
      ><el-input-number
        v-model="rules.rsi_upper"
        :min="1"
        :max="99"
        :disabled="!['ema_rsi', 'rsi_reversion'].includes(rules.strategy_type)"
    /></el-form-item>
    <el-form-item v-if="rules.strategy_type === 'breakout'" label="突破窗口"
      ><el-input-number v-model="rules.breakout_period" :min="5" :max="200"
    /></el-form-item>
    <el-form-item
      v-if="rules.strategy_type === 'bollinger_reversion'"
      label="布林带窗口"
      ><el-input-number v-model="rules.bollinger_period" :min="5" :max="200"
    /></el-form-item>
    <el-form-item
      v-if="rules.strategy_type === 'bollinger_reversion'"
      label="布林带标准差倍数"
      ><el-input-number
        v-model="rules.bollinger_std"
        :min="0.5"
        :max="5"
        :step="0.25"
    /></el-form-item>
    <el-form-item
      :label="
        accountRiskMode ? '止损距离（%，必须启用）' : '止损（%，0 为关闭）'
      "
      ><el-input-number
        v-model="rules.stop_loss_pct"
        :min="accountRiskMode ? 0.1 : 0"
        :max="100"
        :precision="2"
    /></el-form-item>
    <el-form-item label="止盈（%，0 为关闭）"
      ><el-input-number
        v-model="rules.take_profit_pct"
        :min="0"
        :max="1000"
        :precision="2"
    /></el-form-item>
    <el-form-item v-if="!accountRiskMode" label="最大回撤暂停（%，0 为关闭）"
      ><el-input-number
        v-model="rules.max_drawdown_pct"
        :min="0"
        :max="100"
        :precision="2"
    /></el-form-item>
  </div>
  <details :open="!simpleMode" class="advanced-rule-fields">
    <summary v-show="simpleMode">更多过滤条件：成交量、波动与趋势强度</summary>
    <div class="form-grid">
      <el-form-item label="1小时方向过滤（15分钟入场）"
        ><el-switch v-model="rules.hourly_trend_filter"
      /></el-form-item>
      <el-form-item
        v-if="rules.hourly_trend_filter"
        label="1小时均线间距下限（%）"
        ><el-input-number
          v-model="rules.hourly_min_gap_pct"
          :min="0"
          :max="5"
          :step="0.05"
      /></el-form-item>
      <el-form-item label="成交量倍数下限（0 为关闭）"
        ><el-input-number
          v-model="rules.volume_ratio"
          :min="0"
          :max="10"
          :step="0.25"
      /></el-form-item>
      <el-form-item label="成交量均值周期"
        ><el-input-number v-model="rules.volume_period" :min="5" :max="100"
      /></el-form-item>
      <el-form-item label="ATR / 价格上限（%，0 为关闭）"
        ><el-input-number
          v-model="rules.atr_pct_max"
          :min="0"
          :max="100"
          :step="0.25"
      /></el-form-item>
      <el-form-item label="ATR 周期"
        ><el-input-number v-model="rules.atr_period" :min="2" :max="100"
      /></el-form-item>
      <el-form-item label="ADX 趋势强度下限（0 为关闭）"
        ><el-input-number v-model="rules.adx_min" :min="0" :max="100"
      /></el-form-item>
      <el-form-item label="ADX 周期"
        ><el-input-number v-model="rules.adx_period" :min="2" :max="100"
      /></el-form-item>
    </div>
  </details>
  <p v-if="rules.hourly_trend_filter" class="panel-note">
    请将信号周期设为15分钟。完整已收盘1小时K线使用上方周期的有限窗口指数均线判断方向；上涨只允许做多事件，下跌只允许做空事件，间距不足、预热不足或小时数据缺失时等待。固定使用最近“慢EMA＋1”个小时，研究和运行采用相同窗口。慢EMA最多200；交易所止损持续保护，不等待15分钟收盘。
  </p>
  <p v-if="accountRiskMode" class="panel-note">
    模板自动判断多空；账户回撤与每日亏损限制使用下方共享预算设置。暂停状态且空仓时可修改，保存后才生效。
  </p>
  <details class="factor-guide">
    <summary>因子是什么？这些参数怎样影响策略？</summary>
    <p class="panel-note">
      因子是从行情计算出来的特征，不是保证涨跌的原因。EMA和价格动量观察趋势；RSI观察价格强弱；成交量倍数观察量能；ATR观察波动；ADX观察趋势强度。
    </p>
    <p class="panel-note">
      模板决定入场事件和多空方向。成交量、ATR、ADX是过滤条件：有入场事件但过滤未通过，就等待；ADX强或成交量大本身不代表应该做多。阈值为0表示关闭对应过滤。
    </p>
    <p class="panel-note">
      不要把所有条件都调到“看起来最赚钱”。先固定研究区间，用回测里的因子诊断和逐项关闭过滤对照检查，再用后续独立区间验证。过滤太多可能只剩极少数交易，结果不稳定。
    </p>
  </details>
  <p v-if="!accountRiskMode" class="panel-note">
    快 EMA 小于慢 EMA，RSI
    下限小于上限。回测按收盘价检查风控、下一根开盘成交；模拟引擎按轮询价格检查，实际触发时间与成交价可能不同。未模拟杠杆强平或资金费率。
  </p>
</template>
