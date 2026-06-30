import Feather from "@expo/vector-icons/Feather";
import { SFSymbol, SymbolView } from "expo-symbols";
import React from "react";
import { ViewStyle } from "react-native";

type FeatherName = React.ComponentProps<typeof Feather>["name"];

export function AppIcon({
  symbol,
  fallback,
  color,
  size = 18,
  style,
}: {
  symbol: SFSymbol;
  fallback: FeatherName;
  color: string;
  size?: number;
  style?: ViewStyle;
}) {
  return (
    <SymbolView
      accessible={false}
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
      name={symbol}
      fallback={
        <Feather
          accessible={false}
          color={color}
          importantForAccessibility="no-hide-descendants"
          name={fallback}
          size={size}
        />
      }
      resizeMode="scaleAspectFit"
      size={size}
      style={[{ height: size, width: size }, style]}
      tintColor={color}
      type="monochrome"
      weight="medium"
    />
  );
}
