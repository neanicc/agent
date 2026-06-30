import React, { useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleProp,
  Text,
  TextInput,
  TextInputProps,
  TextStyle,
  View,
  ViewStyle,
} from "react-native";
import { theme } from "../theme";
import { AppIcon } from "./AppIcon";

export function Screen({
  children,
  scroll = true,
  contentStyle,
  refreshControl,
}: {
  children: React.ReactNode;
  scroll?: boolean;
  contentStyle?: StyleProp<ViewStyle>;
  refreshControl?: React.ReactElement;
}) {
  const content: ViewStyle = {
    alignSelf: "center",
    gap: theme.space(5),
    maxWidth: 720,
    paddingBottom: theme.space(8),
    paddingHorizontal: theme.space(5),
    paddingTop: theme.space(4),
    width: "100%",
  };

  return (
    <SafeAreaView style={{ backgroundColor: theme.bg, flex: 1 }}>
      {scroll ? (
        <ScrollView
          contentContainerStyle={[content, contentStyle]}
          keyboardShouldPersistTaps="handled"
          refreshControl={refreshControl}
          style={{ flex: 1 }}
        >
          {children}
        </ScrollView>
      ) : (
        <View style={[content, { flex: 1 }, contentStyle]}>{children}</View>
      )}
    </SafeAreaView>
  );
}

export function PageHeader({
  title,
  subtitle,
}: {
  title: string;
  subtitle?: string;
}) {
  return (
    <View style={{ gap: theme.space(2), paddingBottom: theme.space(1) }}>
      <Text
        accessibilityRole="header"
        style={{
          color: theme.text,
          fontFamily: theme.display,
          fontSize: 31,
          letterSpacing: -0.8,
          lineHeight: 35,
        }}
      >
        {title}
      </Text>
      {subtitle ? (
        <Text
          style={{
            color: theme.textDim,
            fontFamily: theme.body,
            fontSize: 16,
            lineHeight: 24,
            maxWidth: 560,
          }}
        >
          {subtitle}
        </Text>
      ) : null}
    </View>
  );
}

export function Section({
  children,
  title,
  accessory,
  style,
  flush = false,
}: {
  children: React.ReactNode;
  title?: string;
  accessory?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  flush?: boolean;
}) {
  return (
    <View
      style={[
        {
          backgroundColor: theme.surface,
          borderColor: theme.border,
          borderRadius: theme.radiusLg,
          borderWidth: 1,
          gap: flush ? 0 : theme.space(3),
          overflow: "hidden",
          padding: flush ? 0 : theme.space(4),
        },
        style,
      ]}
    >
      {title || accessory ? (
        <View
          style={{
            alignItems: "center",
            borderBottomColor: theme.border,
            borderBottomWidth: flush ? 1 : 0,
            flexDirection: "row",
            gap: theme.space(3),
            justifyContent: "space-between",
            minHeight: flush ? 54 : theme.hitTarget,
            paddingHorizontal: flush ? theme.space(4) : 0,
          }}
        >
          {title ? (
            <Text
              style={{
                color: theme.text,
                flex: 1,
                fontFamily: theme.display,
                fontSize: 18,
                lineHeight: 23,
              }}
            >
              {title}
            </Text>
          ) : (
            <View />
          )}
          {accessory}
        </View>
      ) : null}
      {children}
    </View>
  );
}

type ButtonVariant = "primary" | "secondary" | "selected" | "quiet" | "danger";
type ButtonState = "default" | "error" | "success";

export function ActionButton({
  label,
  onPress,
  disabled = false,
  loading = false,
  variant = "primary",
  state = "default",
  leading,
  style,
  textStyle,
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  loading?: boolean;
  variant?: ButtonVariant;
  state?: ButtonState;
  leading?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
}) {
  const [focused, setFocused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const unavailable = disabled || loading;
  const backgroundColor =
    state === "success"
      ? theme.ok
      : state === "error"
        ? theme.danger
        : variant === "primary"
          ? theme.text
          : variant === "selected"
            ? theme.surfaceHigh
          : hovered
            ? theme.surfaceHigh
            : variant === "quiet"
              ? theme.bg2
              : theme.surfaceHigh;
  const color =
    state === "success" || state === "error" || variant === "primary"
      ? theme.accentInk
      : variant === "selected"
        ? theme.accent
      : variant === "danger"
        ? theme.danger
        : theme.text;
  const borderColor =
    focused
      ? theme.focus
      : state === "error" || variant === "danger"
        ? theme.danger
        : state === "success"
          ? theme.ok
          : variant === "primary"
            ? theme.text
            : variant === "selected"
              ? theme.accent
            : theme.borderStrong;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ busy: loading, disabled: unavailable }}
      disabled={unavailable}
      onBlur={() => setFocused(false)}
      onFocus={() => setFocused(true)}
      onHoverIn={() => setHovered(true)}
      onHoverOut={() => setHovered(false)}
      onPress={onPress}
      style={({ pressed }) => [
        {
          alignItems: "center",
          backgroundColor,
          borderColor,
          borderRadius: theme.radius,
          borderWidth: 1,
          flexDirection: "row",
          gap: theme.space(2),
          justifyContent: "center",
          minHeight: theme.controlHeight,
          opacity: unavailable ? 0.52 : pressed ? 0.78 : 1,
          paddingHorizontal: theme.space(4),
          transform: [{ translateY: pressed ? 1 : 0 }],
        },
        style,
      ]}
    >
      {loading ? <ActivityIndicator color={color} size="small" /> : leading}
      <Text
        numberOfLines={1}
        style={[
          {
            color,
            fontFamily: theme.bodySemibold,
            fontSize: 15,
            lineHeight: 19,
          },
          textStyle,
        ]}
      >
        {loading ? "Working…" : label}
      </Text>
    </Pressable>
  );
}

export function LabeledInput({
  label,
  helper,
  error,
  success,
  loading = false,
  style,
  ...props
}: TextInputProps & {
  label: string;
  helper?: string;
  error?: string | null;
  success?: boolean;
  loading?: boolean;
  style?: StyleProp<TextStyle>;
}) {
  const [focused, setFocused] = useState(false);
  const borderColor = error
    ? theme.danger
    : success
      ? theme.ok
      : focused
        ? theme.focus
        : theme.borderStrong;

  return (
    <View style={{ gap: theme.space(1) }}>
      <Text
        style={{
          color: theme.textDim,
          fontFamily: theme.bodyMedium,
          fontSize: 13,
          lineHeight: 17,
        }}
      >
        {label}
      </Text>
      <View>
        <TextInput
          accessibilityLabel={label}
          accessibilityState={{ disabled: props.editable === false }}
          onBlur={(event) => {
            setFocused(false);
            props.onBlur?.(event);
          }}
          onFocus={(event) => {
            setFocused(true);
            props.onFocus?.(event);
          }}
          placeholderTextColor={theme.textMuted}
          {...props}
          style={[
            {
              backgroundColor: theme.surfaceInset,
              borderColor,
              borderRadius: theme.radius,
              borderWidth: 1,
              color: theme.text,
              fontFamily: theme.body,
              fontSize: 16,
              lineHeight: 21,
              minHeight: theme.controlHeight,
              paddingHorizontal: theme.space(3),
              paddingRight: theme.space(10),
              paddingVertical: theme.space(3),
            },
            style,
          ]}
        />
        {loading || error || success ? (
          <View
            pointerEvents="none"
            style={{
              alignItems: "center",
              height: theme.controlHeight,
              justifyContent: "center",
              position: "absolute",
              right: theme.space(3),
            }}
          >
            {loading ? (
              <ActivityIndicator color={theme.accent} size="small" />
            ) : (
              <AppIcon
                color={error ? theme.danger : theme.ok}
                fallback={error ? "alert-circle" : "check-circle"}
                size={17}
                symbol={error ? "exclamationmark.circle.fill" : "checkmark.circle.fill"}
              />
            )}
          </View>
        ) : null}
      </View>
      <Text
        accessibilityLiveRegion="polite"
        style={{
          color: error ? theme.danger : theme.textMuted,
          fontFamily: theme.body,
          fontSize: 13,
          lineHeight: 18,
          minHeight: 18,
        }}
      >
        {error || helper || " "}
      </Text>
    </View>
  );
}

export function StatusBadge({
  label,
  color = theme.accent,
}: {
  label: string;
  color?: string;
}) {
  return (
    <View
      style={{
        alignItems: "center",
        alignSelf: "flex-start",
        backgroundColor: theme.bg2,
        borderColor: theme.border,
        borderRadius: theme.radiusFull,
        borderWidth: 1,
        flexDirection: "row",
        gap: theme.space(2),
        minHeight: 28,
        paddingHorizontal: theme.space(2),
      }}
    >
      <View
        accessibilityElementsHidden
        style={{ backgroundColor: color, borderRadius: 3, height: 6, width: 6 }}
      />
      <Text
        numberOfLines={1}
        style={{
          color: theme.text,
          fontFamily: theme.bodyMedium,
          fontSize: 12,
          lineHeight: 16,
        }}
      >
        {label}
      </Text>
    </View>
  );
}

export function EmptyState({
  title,
  body,
  symbol,
  fallback,
  action,
}: {
  title: string;
  body: string;
  symbol: React.ComponentProps<typeof AppIcon>["symbol"];
  fallback: React.ComponentProps<typeof AppIcon>["fallback"];
  action?: React.ReactNode;
}) {
  return (
    <View
      style={{
        alignItems: "flex-start",
        gap: theme.space(2),
        paddingVertical: theme.space(4),
      }}
    >
      <AppIcon color={theme.textDim} fallback={fallback} size={22} symbol={symbol} />
      <Text
        style={{
          color: theme.text,
          fontFamily: theme.bodySemibold,
          fontSize: 16,
          lineHeight: 21,
        }}
      >
        {title}
      </Text>
      <Text
        style={{
          color: theme.textDim,
          fontFamily: theme.body,
          fontSize: 14,
          lineHeight: 21,
        }}
      >
        {body}
      </Text>
      {action ? <View style={{ paddingTop: theme.space(2) }}>{action}</View> : null}
    </View>
  );
}
