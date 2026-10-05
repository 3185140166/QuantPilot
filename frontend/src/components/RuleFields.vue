<script setup lang="ts">
import type { StrategyRules } from "../types";
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
        :max="500"
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
    <el-form-item label="止损（%，0 为关闭）"
      ><el-input-number
        v-model="rules.stop_loss_pct"
        :min="0"
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
    <el-form-item label="最大回撤暂停（%，0 为关闭）"
      ><el-input-number
        v-model="rules.max_drawdown_pct"
        :min="0"
        :max="100"
        :precision="2"
    /></el-form-item>
  </div>
  <p class="panel-note">
    快 EMA 小于慢 EMA，RSI
    下限小于上限。回测按收盘价检查风控、下一根开盘成交；模拟引擎按轮询价格检查，实际触发时间与成交价可能不同。未模拟杠杆强平或资金费率。
  </p>
</template>
