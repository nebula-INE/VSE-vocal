// VoiceGalleryComponent.h
//
// フェーズ3「音源ブラウザ（ボイスギャラリー）の統合」。
// modules/gui/widgets.py の VoiceCardWidget と
// modules/gui/mixins/voice_management_mixin.py の update_voice_list() /
// on_voice_selected() をJUCEネイティブで再実装したもの。
//
// Python版との違い:
//   - アイコン画像は扱わず、名前から決定的に生成したアクセントカラーの
//     スウォッチで代替している（icon.pngの読み込み・キャッシュ管理は
//     このフェーズのスコープ外とした簡略化）。
//   - プラグインエディタの限られた縦幅に収まるよう、3列グリッドではなく
//     横一列（Viewportで横スクロール）のレイアウトにしている。
//
// 使い方（PluginEditor.h側）:
//   voiceGalleryViewport.setViewedComponent (&voiceGallery, false);
//   voiceGalleryViewport.setScrollBarsShown (false, true);
//   voiceGallery.onVoiceSelected = [this] (const VoiceInfo& info)
//   {
//       if (info.isEmbedded)
//           ; // TODO: 内蔵音源に切り替えるAPIをVoseAudioProcessorに追加する必要がある
//       else
//           processor.loadVoiceDirectory (info.directory);
//   };
//   voiceGallery.rescan();

#pragma once

#include <juce_gui_basics/juce_gui_basics.h>
#include "VoiceInfo.h"
#include "VoiceScanner.h"
#include "VoseColourIds.h"
#include <vector>
#include <memory>
#include <functional>

// 1枚のボイスカード。VoiceCardWidget(QFrame)相当。
class VoiceCardComponent : public juce::Component
{
public:
    VoiceCardComponent (VoiceInfo infoIn, juce::Colour accentIn)
        : info (std::move (infoIn)), accentColour (accentIn)
    {
        setMouseCursor (juce::MouseCursor::PointingHandCursor);
    }

    void setSelected (bool s) { if (selected != s) { selected = s; repaint(); } }
    bool isSelected() const { return selected; }
    const VoiceInfo& getInfo() const { return info; }

    std::function<void()> onClicked;

    void paint (juce::Graphics& g) override
    {
        auto& lf = getLookAndFeel();
        auto bounds = getLocalBounds().toFloat().reduced (2.0f);

        const auto cardBg = lf.findColour (VoseColourIds::galleryCardBackground);
        const auto borderCol = selected ? accentColour : lf.findColour (VoseColourIds::galleryCardBorder);
        const float borderWidth = selected ? 2.0f : 1.0f;
        const float bgAlpha = selected ? 0.9f : 0.6f;

        g.setColour (cardBg.withAlpha (bgAlpha));
        g.fillRoundedRectangle (bounds, 10.0f);
        g.setColour (borderCol);
        g.drawRoundedRectangle (bounds, 10.0f, borderWidth);

        // アイコン代わりのアクセントカラー・スウォッチ
        auto swatchArea = bounds.reduced (14.0f);
        swatchArea = swatchArea.removeFromTop (swatchArea.getHeight() * 0.55f);
        const float swatchSize = juce::jmin (swatchArea.getWidth(), swatchArea.getHeight());
        juce::Rectangle<float> swatch (0, 0, swatchSize, swatchSize);
        swatch.setCentre (swatchArea.getCentre());
        g.setColour (accentColour);
        g.fillEllipse (swatch);

        if (info.isEmbedded)
        {
            g.setColour (juce::Colours::white);
            g.setFont (juce::Font (14.0f, juce::Font::bold));
            g.drawFittedText ("★", swatch.toNearestInt(), juce::Justification::centred, 1);
        }

        // 名前ラベル
        g.setColour (lf.findColour (juce::Label::textColourId));
        g.setFont (11.0f);
        auto labelArea = bounds.reduced (6.0f, 2.0f).removeFromBottom (bounds.getHeight() * 0.32f);
        g.drawFittedText (info.name, labelArea.toNearestInt(), juce::Justification::centred, 2);
    }

    void mouseUp (const juce::MouseEvent& event) override
    {
        if (event.mods.isLeftButtonDown() && getLocalBounds().contains (event.getPosition()))
            if (onClicked != nullptr)
                onClicked();
    }

private:
    VoiceInfo info;
    juce::Colour accentColour;
    bool selected = false;
};

// カード一覧（横一列、Viewportで横スクロールする前提）。
class VoiceGalleryComponent : public juce::Component
{
public:
    VoiceGalleryComponent() = default;

    // OS標準の音源フォルダ + voice_banks を再スキャンしてカードを作り直す。
    void rescan()
    {
        rebuildCards (VoiceScanner::scanInstalledVoices());
    }

    void setSelectedByName (const juce::String& name)
    {
        for (auto& card : cards)
            card->setSelected (card->getInfo().name == name);
        repaint();
    }

    std::function<void (const VoiceInfo&)> onVoiceSelected;

    void resized() override
    {
        int x = kCardSpacing;
        for (auto& card : cards)
        {
            card->setBounds (x, 0, kCardWidth, getHeight());
            x += kCardWidth + kCardSpacing;
        }
    }

private:
    static constexpr int kCardWidth = 96;
    static constexpr int kCardSpacing = 8;

    static juce::Colour accentColourForName (const VoiceInfo& info)
    {
        if (info.isEmbedded)
            return juce::Colour (0xff0a84ff); // 内蔵音源は常に固定のアクセント色（VoseLookAndFeelのaccent系に近い値）

        const float hue = (float) (((uint32_t) info.name.hashCode()) % 360u) / 360.0f;
        return juce::Colour::fromHSV (hue, 0.55f, 0.85f, 1.0f);
    }

    void rebuildCards (std::vector<VoiceInfo> infos)
    {
        cards.clear();
        removeAllChildren();

        for (auto& info : infos)
        {
            auto card = std::make_unique<VoiceCardComponent> (info, accentColourForName (info));
            auto* raw = card.get();
            card->onClicked = [this, raw]
            {
                for (auto& c : cards)
                    c->setSelected (c.get() == raw);
                if (onVoiceSelected != nullptr)
                    onVoiceSelected (raw->getInfo());
            };
            addAndMakeVisible (*card);
            cards.push_back (std::move (card));
        }

        setSize ((int) infos.size() * (kCardWidth + kCardSpacing) + kCardSpacing,
                 getHeight() > 0 ? getHeight() : 100);
        resized();
    }

    std::vector<std::unique_ptr<VoiceCardComponent>> cards;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (VoiceGalleryComponent)
};
