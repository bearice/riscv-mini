// SPDX-License-Identifier: GPL-2.0
/* PT8211 stereo DMA playback and paired I2S microphone snapshot capture. */
#include <linux/dma-mapping.h>
#include <linux/hrtimer.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <sound/core.h>
#include <sound/pcm.h>
#include <sound/pcm_params.h>

struct mini_audio {
	void __iomem *out,*mic;
	struct snd_card *card;
	struct snd_pcm *pcm;
	struct snd_pcm_substream *streams[2];
	struct hrtimer timer;
	spinlock_t lock;
	bool running[2], dead;
	u32 producer,played,captured,notified[2];
	snd_pcm_uframes_t appl;
};
static const struct snd_pcm_hardware mini_hw={
	.info=SNDRV_PCM_INFO_INTERLEAVED|SNDRV_PCM_INFO_MMAP|SNDRV_PCM_INFO_MMAP_VALID|SNDRV_PCM_INFO_BLOCK_TRANSFER|SNDRV_PCM_INFO_NO_REWINDS,
	.formats=SNDRV_PCM_FMTBIT_S16_LE,.rates=SNDRV_PCM_RATE_48000,.rate_min=48000,.rate_max=48000,
	.channels_min=2,.channels_max=2,.buffer_bytes_max=65536,
	.period_bytes_min=1024,.period_bytes_max=16384,.periods_min=2,.periods_max=16,
};
static enum hrtimer_restart mini_audio_tick(struct hrtimer *timer)
{
	struct mini_audio *a=container_of(timer,struct mini_audio,timer);
	struct snd_pcm_substream *notify[2]={NULL,NULL};
	unsigned long flags; int i;
	spin_lock_irqsave(&a->lock,flags);
	if(a->running[0]) {
		a->played=readl(a->out+20);
		if(a->played-a->notified[0]>=a->streams[0]->runtime->period_size) {
			a->notified[0]=a->played; notify[0]=a->streams[0];
		}
	}
	if(a->running[1]) {
		struct snd_pcm_runtime *r=a->streams[1]->runtime;
		u32 level=readl(a->mic+20)&0xffff;
		/* Drain at most the hardware's 512 paired frames per tick. */
		for(i=0;i<min(level,512u);i++) {
			u32 *sample=(void *)(r->dma_area+(a->captured%r->buffer_size)*8);
			sample[0]=readl(a->mic+16); sample[1]=readl(a->mic+40);
			writel(1,a->mic+12); a->captured++;
		}
		if(!(readl(a->mic+24)&1) && !(readl(a->mic+20)&0xffff)) writel(1,a->mic+4);
		if(a->captured-a->notified[1]>=r->period_size) {
			a->notified[1]=a->captured; notify[1]=a->streams[1];
		}
	}
	spin_unlock_irqrestore(&a->lock,flags);
	for(i=0;i<2;i++) if(notify[i]) snd_pcm_period_elapsed(notify[i]);
	if(!READ_ONCE(a->running[0]) && !READ_ONCE(a->running[1])) return HRTIMER_NORESTART;
	hrtimer_forward_now(timer,ms_to_ktime(1)); return HRTIMER_RESTART;
}
static int mini_pcm_open(struct snd_pcm_substream *s)
{
	struct mini_audio *a=snd_pcm_substream_chip(s);
	s->runtime->hw=mini_hw;
	if(s->stream==SNDRV_PCM_STREAM_CAPTURE) s->runtime->hw.formats=SNDRV_PCM_FMTBIT_S24_LE;
	a->streams[s->stream]=s;
	return snd_pcm_hw_constraint_integer(s->runtime,SNDRV_PCM_HW_PARAM_PERIODS);
}
static int mini_pcm_close(struct snd_pcm_substream *s)
{
	struct mini_audio *a=snd_pcm_substream_chip(s); unsigned long flags;
	spin_lock_irqsave(&a->lock,flags); a->running[s->stream]=false; spin_unlock_irqrestore(&a->lock,flags);
	/* Cancel outside the lock, waiting for any period notification to finish. */
	hrtimer_cancel(&a->timer); a->streams[s->stream]=NULL;
	if(a->running[!s->stream]) hrtimer_start(&a->timer,ms_to_ktime(1),HRTIMER_MODE_REL);
	return 0;
}
static int mini_pcm_prepare(struct snd_pcm_substream *s)
{
	struct mini_audio *a=snd_pcm_substream_chip(s); struct snd_pcm_runtime *r=s->runtime;
	u32 busy; int ret;
	if(a->dead) return -EIO;
	a->notified[s->stream]=0;
	if(s->stream==SNDRV_PCM_STREAM_PLAYBACK) {
		writel(0,a->out); writel(1,a->out+4);
		ret=readl_poll_timeout(a->out+12,busy,!(busy&BIT(19)),10,100000);
		if(ret) { a->dead=true; return ret; }
		writel(1,a->out+4);
		if(!r->dma_addr || r->dma_addr+frames_to_bytes(r,r->buffer_size)>0x07e00000) return -ERANGE;
		writel(r->dma_addr,a->out+36); writel(r->buffer_size,a->out+40); writel(0,a->out+44);
		a->producer=a->played=0; a->appl=r->control->appl_ptr;
	} else { writel(0,a->mic); writel(1,a->mic+8); a->captured=0; }
	return 0;
}
static int mini_pcm_ack(struct snd_pcm_substream *s)
{
	struct mini_audio *a=snd_pcm_substream_chip(s); struct snd_pcm_runtime *r=s->runtime;
	snd_pcm_uframes_t appl=READ_ONCE(r->control->appl_ptr),delta;
	if(s->stream!=SNDRV_PCM_STREAM_PLAYBACK) return 0;
	delta=appl>=a->appl?appl-a->appl:appl+r->boundary-a->appl;
	if(delta>r->buffer_size) return -EINVAL;
	a->producer+=delta; a->appl=appl;
	dma_wmb(); writel(a->producer,a->out+44); return 0;
}
static int mini_pcm_trigger(struct snd_pcm_substream *s,int command)
{
	struct mini_audio *a=snd_pcm_substream_chip(s); unsigned long flags; int ret=0;
	spin_lock_irqsave(&a->lock,flags);
	switch(command) {
	case SNDRV_PCM_TRIGGER_START:
		a->running[s->stream]=true;
		if(s->stream==SNDRV_PCM_STREAM_PLAYBACK) writel(3,a->out);
		else { writel(5,a->mic); writel(1,a->mic+4); }
		hrtimer_start(&a->timer,ms_to_ktime(1),HRTIMER_MODE_REL); break;
	case SNDRV_PCM_TRIGGER_STOP:
		a->running[s->stream]=false;
		if(s->stream==SNDRV_PCM_STREAM_PLAYBACK) writel(0,a->out); else writel(0,a->mic);
		break;
	default: ret=-EINVAL;
	}
	spin_unlock_irqrestore(&a->lock,flags); return ret;
}
static snd_pcm_uframes_t mini_pcm_pointer(struct snd_pcm_substream *s)
{
	struct mini_audio *a=snd_pcm_substream_chip(s);
	return (s->stream==SNDRV_PCM_STREAM_PLAYBACK?readl(a->out+20):READ_ONCE(a->captured))%s->runtime->buffer_size;
}
static const struct snd_pcm_ops mini_pcm_ops={
	.open=mini_pcm_open,.close=mini_pcm_close,.ioctl=snd_pcm_lib_ioctl,
	.prepare=mini_pcm_prepare,.trigger=mini_pcm_trigger,.pointer=mini_pcm_pointer,.ack=mini_pcm_ack,
};
static ssize_t stats_show(struct device *dev,struct device_attribute *attr,char *buf)
{
	struct mini_audio *a=dev_get_drvdata(dev);
	return sysfs_emit(buf,"played=%u fetched=%u underruns=%u errors=%u mic_samples=%u captured=%u mic_overruns=%u\n",
		readl(a->out+20),readl(a->out+48),readl(a->out+24),(readl(a->out+12)>>16)&7,
		readl(a->mic+28),a->captured,readl(a->mic+36));
}
static DEVICE_ATTR_RO(stats);
static int mini_audio_probe(struct platform_device *pdev)
{
	struct snd_card *card; struct mini_audio *a; u32 busy; int ret;
	ret=dma_set_mask_and_coherent(&pdev->dev,DMA_BIT_MASK(32)); if(ret) return ret;
	ret=snd_card_new(&pdev->dev,-1,"Mini",THIS_MODULE,sizeof(*a),&card); if(ret) return ret;
	a=card->private_data; a->card=card;
	a->out=devm_platform_ioremap_resource(pdev,0); a->mic=devm_platform_ioremap_resource(pdev,1);
	if(IS_ERR(a->out)||IS_ERR(a->mic)) { ret=-ENODEV; goto free; }
	writel(0,a->out); writel(1,a->out+4); writel(0,a->mic);
	ret=readl_poll_timeout(a->out+12,busy,!(busy&BIT(19)),10,100000); if(ret) goto free;
	spin_lock_init(&a->lock); hrtimer_init(&a->timer,CLOCK_MONOTONIC,HRTIMER_MODE_REL);
	a->timer.function=mini_audio_tick;
	ret=snd_pcm_new(card,"PT8211 and I2S",0,1,1,&a->pcm); if(ret) goto free;
	a->pcm->private_data=a; strscpy(a->pcm->name,"riscv-mini stereo PCM",sizeof(a->pcm->name));
	snd_pcm_set_ops(a->pcm,SNDRV_PCM_STREAM_PLAYBACK,&mini_pcm_ops);
	snd_pcm_set_ops(a->pcm,SNDRV_PCM_STREAM_CAPTURE,&mini_pcm_ops);
	ret=snd_pcm_set_managed_buffer_all(a->pcm,SNDRV_DMA_TYPE_DEV,&pdev->dev,65536,65536); if(ret) goto free;
	strscpy(card->driver,"riscv-mini",sizeof(card->driver)); strscpy(card->shortname,"riscv-mini audio",sizeof(card->shortname));
	strscpy(card->longname,"PT8211 stereo DAC and dual I2S microphone",sizeof(card->longname));
	ret=snd_card_register(card); if(ret) goto free;
	platform_set_drvdata(pdev,a); ret=device_create_file(&pdev->dev,&dev_attr_stats);
	if(!ret) return 0;
free:
	snd_card_free(card); return ret;
}
static void mini_audio_shutdown(struct platform_device *pdev)
{
	struct mini_audio *a=platform_get_drvdata(pdev); u32 busy;
	snd_card_disconnect(a->card); hrtimer_cancel(&a->timer);
	writel(0,a->out); writel(0,a->mic);
	if(readl_poll_timeout(a->out+12,busy,!(busy&BIT(19)),10,100000)) dev_err(&pdev->dev,"audio DMA drain timed out; buffers retained\n");
}
static const struct of_device_id mini_audio_match[]={ {.compatible="riscv-mini,audio"},{} };
static struct platform_driver mini_audio_driver={.probe=mini_audio_probe,.shutdown=mini_audio_shutdown,
	.driver={.name="mini-audio",.of_match_table=mini_audio_match,.suppress_bind_attrs=true}};
builtin_platform_driver(mini_audio_driver);
