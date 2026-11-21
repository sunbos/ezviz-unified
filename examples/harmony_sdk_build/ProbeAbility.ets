import { UIAbility } from '@kit.AbilityKit';
import { EZOpenSDK, EZPlayer, EZPlaybackRate } from '@ezviz/ezopensdk';

export default class ProbeAbility extends UIAbility {
  public async checkDeclarations(
    player: EZPlayer,
    token: string,
    verifyCode: string,
    position: Date,
    rate: EZPlaybackRate,
    mode: number
  ): Promise<boolean> {
    await EZOpenSDK.setAccessToken(token);
    EZOpenSDK.getDeviceList(0, 1, (deviceList, totalCount, error): void => {});
    player.setPlayVerifyCode(verifyCode);
    await player.seekPlayback(position);
    return player.setPlaybackRate(rate, mode);
  }
}
