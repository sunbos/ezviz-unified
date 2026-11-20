package org.example.ezvizprobe;

import android.view.SurfaceHolder;
import com.videogo.openapi.EZConstants.EZPlaybackRate;
import com.videogo.openapi.EZOpenSDK;
import com.videogo.openapi.EZPlayer;
import java.util.Calendar;

/**
 * Build-only probe: references each selected {@link EZOpenSDK} and
 * {@link EZPlayer} overload once so the SDK surface must resolve at compile
 * time. Never instantiated or invoked.
 */
final class NativeSDKProbe {

    private NativeSDKProbe() {
    }

    /** Never invoked; exists solely so the referenced overloads compile. */
    private static void probeOverloads(
            EZOpenSDK sdk,
            EZPlayer player,
            String apiUrl,
            String authUrl,
            String accessToken,
            String playVerifyCode,
            SurfaceHolder surfaceHolder,
            Calendar seekTime,
            EZPlaybackRate playbackRate,
            int mode) throws com.videogo.exception.BaseException {
        sdk.setServerUrl(apiUrl, authUrl);
        sdk.setAccessToken(accessToken);
        sdk.getDeviceList(0, 1);
        player.setPlayVerifyCode(playVerifyCode);
        player.seekPlayback(seekTime);
        player.setSurfaceHold(surfaceHolder);
        player.setSurfaceHold(surfaceHolder, mode);
        player.setPlaybackRate(playbackRate);
        player.setPlaybackRate(playbackRate, mode);
    }
}
