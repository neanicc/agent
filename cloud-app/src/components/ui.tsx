import React from "react";
import { Pressable, ScrollView, Text, TextStyle, View, ViewStyle } from "react-native";
import { theme } from "../theme";

export function AmbientBackground() {
  return (
    <View pointerEvents="none" style={{ position: "absolute", top: 0, right: 0, bottom: 0, left: 0, backgroundColor: theme.bg }}>
      <View style={{ position: "absolute", width: 280, height: 280, borderRadius: 140, backgroundColor: "rgba(56,189,248,0.20)", top: -90, right: -90 }} />
      <View style={{ position: "absolute", width: 240, height: 240, borderRadius: 120, backgroundColor: "rgba(192,132,252,0.16)", top: 160, left: -120 }} />
      <View style={{ position: "absolute", width: 220, height: 220, borderRadius: 110, backgroundColor: "rgba(52,211,153,0.10)", bottom: 120, right: -110 }} />
    </View>
  );
}

export function Screen({ children, scroll = true }: { children: React.ReactNode; scroll?: boolean }) {
  const content = { padding: theme.space(5), paddingTop: theme.space(14), paddingBottom: theme.space(8), gap: theme.space(4) };
  if (!scroll) return <View style={{ flex: 1, backgroundColor: theme.bg }}><AmbientBackground /><View style={[{ flex: 1 }, content]}>{children}</View></View>;
  return <View style={{ flex: 1, backgroundColor: theme.bg }}><AmbientBackground /><ScrollView style={{ flex: 1 }} contentContainerStyle={content}>{children}</ScrollView></View>;
}

export function Card({ children, style }: { children: React.ReactNode; style?: ViewStyle }) {
  return <View style={[{ backgroundColor: theme.surfaceGlass, borderWidth: 1, borderColor: theme.border, borderRadius: theme.radiusLg, padding: theme.space(4), gap: theme.space(3), overflow: "hidden" }, theme.shadow, style]}>{children}</View>;
}

export function Hero({ eyebrow, title, subtitle }: { eyebrow?: string; title: string; subtitle: string }) {
  return <View style={{ gap: theme.space(2), marginBottom: theme.space(1) }}>{eyebrow ? <Text style={{ color: theme.accent, fontSize: 12, fontWeight: "800", letterSpacing: 1.6, textTransform: "uppercase" }}>{eyebrow}</Text> : null}<Text style={{ color: theme.text, fontSize: 32, lineHeight: 38, fontWeight: "900", letterSpacing: -0.8 }}>{title}</Text><Text style={{ color: theme.textDim, fontSize: 14, lineHeight: 21 }}>{subtitle}</Text></View>;
}

export function Pill({ label, color = theme.accent, muted }: { label: string; color?: string; muted?: boolean }) {
  return <Text style={{ color: muted ? theme.textDim : color, borderColor: muted ? theme.borderStrong : color, borderWidth: 1, borderRadius: 999, paddingHorizontal: theme.space(3), paddingVertical: theme.space(1), fontSize: 11, fontWeight: "800", overflow: "hidden" }}>{label}</Text>;
}

export function CTAButton({ label, onPress, disabled, color = theme.accent, textStyle }: { label: string; onPress: () => void; disabled?: boolean; color?: string; textStyle?: TextStyle }) {
  return <Pressable onPress={onPress} disabled={disabled} style={{ backgroundColor: color, padding: theme.space(4), borderRadius: theme.radius, alignItems: "center", opacity: disabled ? 0.58 : 1 }}><Text style={[{ color: "#020617", fontWeight: "900", fontSize: 14 }, textStyle]}>{label}</Text></Pressable>;
}
