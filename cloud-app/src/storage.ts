import AsyncStorage from "@react-native-async-storage/async-storage";

const URL_KEY = "loopguard.serverUrl";

export async function saveServerUrl(url: string): Promise<void> {
  try {
    await AsyncStorage.setItem(URL_KEY, url);
  } catch {
    /* storage unavailable — non-fatal */
  }
}

export async function loadServerUrl(): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(URL_KEY);
  } catch {
    return null;
  }
}
