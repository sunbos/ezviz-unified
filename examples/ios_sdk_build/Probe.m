@import Foundation;
@import EZOpenSDKFramework;

/* Retained for link-time symbol coverage; intentionally never invoked. */
static __attribute__((used)) void Probe(EZPlayer *player,
                                        NSString *accessToken,
                                        NSString *verificationCode,
                                        NSDate *position,
                                        EZPlaybackRate rate) {
    (void)[EZPlayer class];
    (void)[EZOpenSDK class];

    [EZOpenSDK setAccessToken:accessToken];

    NSURLSessionDataTask *task =
        [EZOpenSDK getDeviceList:0
                        pageSize:1
                      completion:^(NSArray *deviceList,
                                   NSInteger totalCount,
                                   NSError * _Nullable error) {
                          (void)deviceList;
                          (void)totalCount;
                          (void)error;
                      }];
    (void)task;

    [player setPlayVerifyCode:verificationCode];
    [player seekPlayback:position];

    BOOL accepted = [player setPlaybackRate:rate mode:0];
    (void)accepted;
}

int main(void) {
    return 0;
}
